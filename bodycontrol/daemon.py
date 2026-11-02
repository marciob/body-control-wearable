"""Always-connected collector for WHOOP 5.0.

While the strap is in range it keeps one BLE link open and:
  - with `imu=True` only, streams the live 100 Hz six-axis IMU. Off by default: with the
    R22 flags set, the strap stores the same data as history v21 (byte for byte, except
    the type byte), and the live stream about doubled the battery drain,
  - drains the strap's history every `drain_every_min` (flags first, acked chunks);
    a drain that hits its time cap is followed by another at once, until the strap
    reports the history complete,
  - reads the battery every `battery_every_min`, to measure the cost of the stream.
When the strap goes out of range it reconnects on its own. Every GATT call has a
timeout and two failed battery reads in a row count as a dead link: the daemon drops
the link and rebuilds it instead of waiting forever. All frames go to the
`frame` table; the hourly launchd job archives them to S3 and pushes decoded rows.
On exit (SIGTERM / Ctrl-C) it sends the IMU stop commands.
"""

from __future__ import annotations

import asyncio
import signal
import time

from bleak import BleakClient

from . import frame as fr
from . import uuids
from .ble import find_whoop
from .deep import ENABLE_SEQUENCE, flag_frame
from .imu import START_RAW_DATA, STOP_RAW_DATA, TOGGLE_IMU_MODE
from .records import decode_v18, hist_version
from .store import Store, now_ms


GATT_TIMEOUT_S = 30.0


def _log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


class Session:
    def __init__(self, client: BleakClient, store: Store, session_id: int) -> None:
        self.client = client
        self.store = store
        self.sid = session_id
        self.seq = 0
        self.reasm: dict[str, fr.Reassembler] = {}
        self.loop = asyncio.get_running_loop()
        self.acks: asyncio.Queue[bytes] = asyncio.Queue()
        self.drain_done = asyncio.Event()
        self.last_hist = 0.0
        self.gatt_timeout_s = GATT_TIMEOUT_S
        self.n = {"frames": 0, "imu": 0, "hist": 0, "decoded": 0, "chunks": 0}

    def next_seq(self) -> int:
        self.seq = (self.seq + 1) & 0xFF
        return self.seq

    async def _write(self, data: bytes) -> None:
        """Write one command frame. A link that never answers raises TimeoutError; the caller reconnects."""
        await asyncio.wait_for(
            self.client.write_gatt_char(uuids.WHOOP5_CMD_WRITE, data, response=True), self.gatt_timeout_s
        )

    async def send(self, cmd: int, payload: bytes) -> None:
        await self._write(fr.build_command(cmd, seq=self.next_seq(), payload=payload))

    def on_notify(self, sender, data: bytearray) -> None:
        key = str(getattr(sender, "uuid", sender)).lower()
        for raw in self.reasm.setdefault(key, fr.Reassembler()).feed(bytes(data)):
            f = fr.parse_frame(raw)
            if f is None:
                continue
            self.store.add_frame_blob(self.sid, now_ms(), int(key[4:8], 16), f.type, f.crc_ok, raw)
            self.n["frames"] += 1
            if not f.crc_ok:
                continue
            if f.type == fr.PacketType.REALTIME_RAW_DATA:
                self.n["imu"] += 1
            elif f.type == fr.PacketType.HISTORICAL_DATA:
                self.n["hist"] += 1
                self.last_hist = time.time()
                self.store.bump_hist_count(self.sid, hist_version(raw))
                rec = decode_v18(raw)
                if rec:
                    self.store.add_hist18(rec)
                    self.n["decoded"] += 1
            elif f.type in (fr.PacketType.METADATA, fr.PacketType.PUFFIN_METADATA):
                token = fr.history_end_data(raw)
                if token is not None:
                    self.loop.call_soon_threadsafe(self.acks.put_nowait, token)
                elif raw[fr.META_TYPE_OFF] == fr.MetaType.HISTORY_COMPLETE:
                    self.loop.call_soon_threadsafe(self.drain_done.set)

    async def open(self) -> None:
        await asyncio.wait_for(self.client.write_gatt_char(uuids.WHOOP5_CMD_WRITE, fr.CLIENT_HELLO, response=True), 120)
        for u in uuids.WHOOP5_NOTIFY:
            await self.client.start_notify(u, self.on_notify)
        await self._write(fr.CLIENT_HELLO)
        await asyncio.sleep(1.0)

    async def imu(self, on: bool) -> None:
        if on:
            await self.send(START_RAW_DATA, b"\x01")
            await self.send(TOGGLE_IMU_MODE, b"\x01\x01")
        else:
            await self.send(STOP_RAW_DATA, b"\x01")
            await self.send(TOGGLE_IMU_MODE, b"\x01\x00")

    async def battery(self) -> int | None:
        try:
            raw = await asyncio.wait_for(self.client.read_gatt_char(uuids.BATTERY_LEVEL), self.gatt_timeout_s)
            pct = int(bytes(raw)[0])
        except Exception:
            return None
        self.store.add_battery(self.sid, now_ms(), pct)
        self.store.commit()
        return pct

    async def drain(self, idle_s: float = 20.0, max_s: float = 900.0) -> str:
        """One history drain. The ack goes out only after the chunk is committed.

        Returns "complete" (the strap sent HISTORY_COMPLETE), "idle" (no record for
        `idle_s`) or "capped" (`max_s` ran out; the strap still holds records).
        """
        for name, value in ENABLE_SEQUENCE:
            await self._write(flag_frame(name, value, self.next_seq()))
            await asyncio.sleep(0.05)
        self.drain_done.clear()
        start = self.last_hist = time.time()
        before = dict(self.n)
        await self.send(fr.Command.SEND_HISTORICAL_DATA, b"\x00")
        outcome = "complete"
        while not self.drain_done.is_set():
            if time.time() - self.last_hist > idle_s:
                outcome = "idle"
                break
            if time.time() - start > max_s:
                outcome = "capped"
                break
            try:
                token = await asyncio.wait_for(self.acks.get(), 1.0)
            except asyncio.TimeoutError:
                continue
            self.store.commit()
            await self._write(fr.build_history_ack(token, seq=self.next_seq()))
            self.n["chunks"] += 1
        self.store.commit()
        _log(
            f"drain: {self.n['hist'] - before['hist']} history records, "
            f"{self.n['chunks'] - before['chunks']} chunks acked, {time.time() - start:.0f}s, {outcome}"
        )
        return outcome


def drain_delay_s(outcome: str, drain_every_min: float) -> float:
    """Seconds to wait before the next drain. A capped drain left records on the strap: go again now."""
    return 0.0 if outcome == "capped" else drain_every_min * 60


async def run_daemon(db: str, address: str | None, drain_every_min: float = 15.0, battery_every_min: float = 15.0,
                     imu: bool = False) -> int:
    store = Store(db)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    _log(f"daemon start: imu={'on' if imu else 'off'}, drain every {drain_every_min:g} min, battery every {battery_every_min:g} min")
    backoff = 5.0
    while not stop.is_set():
        dev = await find_whoop(timeout=15.0, address=address)
        if dev is None:
            await asyncio.wait([asyncio.create_task(stop.wait())], timeout=backoff)
            backoff = min(backoff * 2, 120.0)
            continue
        backoff = 5.0
        gone = asyncio.Event()
        try:
            async with BleakClient(dev, timeout=20.0, disconnected_callback=lambda _c: loop.call_soon_threadsafe(gone.set)) as client:
                s = Session(client, store, store.start_session(dev.name, dev.address))
                await s.open()
                pct = await s.battery()
                _log(f"connected, session {s.sid}, battery {pct}%")
                outcome = await s.drain()
                if imu:
                    await s.imu(True)
                    _log("IMU stream on")
                next_drain = time.time() + drain_delay_s(outcome, drain_every_min)
                next_batt = time.time() + battery_every_min * 60
                next_report = time.time() + 600
                batt_fail = 0
                try:
                    while not stop.is_set() and not gone.is_set():
                        await asyncio.wait(
                            [asyncio.create_task(stop.wait()), asyncio.create_task(gone.wait())],
                            timeout=5.0, return_when=asyncio.FIRST_COMPLETED,
                        )
                        store.commit()
                        now = time.time()
                        if now >= next_batt:
                            next_batt = now + battery_every_min * 60
                            pct = await s.battery()
                            _log(f"battery {pct}%")
                            batt_fail = 0 if pct is not None else batt_fail + 1
                            if batt_fail >= 2:
                                raise ConnectionError("two battery reads failed; the link is dead")
                        if now >= next_drain:
                            outcome = await s.drain()
                            next_drain = time.time() + drain_delay_s(outcome, drain_every_min)
                        if now >= next_report:
                            next_report = now + 600
                            _log(f"frames {s.n['frames']}, IMU buffers {s.n['imu']}, history {s.n['hist']}")
                finally:
                    if imu and not gone.is_set():
                        try:
                            await s.imu(False)
                            _log("IMU stream off")
                        except Exception as e:
                            _log(f"IMU stop failed: {type(e).__name__}: {e}")
                    store.commit()
            if gone.is_set():
                _log("strap disconnected; reconnecting when in range")
        except Exception as e:
            _log(f"link error: {type(e).__name__}: {e}")
            store.commit()
            await asyncio.sleep(5.0)
    store.close()
    _log("daemon stopped")
    return 0
