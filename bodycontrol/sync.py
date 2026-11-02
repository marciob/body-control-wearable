"""History offload for WHOOP 5.0.

Flow (community-documented, hardware-verified on 5.0):
  1. confirmed write of CLIENT_HELLO (needs the bond)
  2. subscribe the vendor notify channels
  3. SEND_HISTORICAL_DATA (22), payload 0x00
  4. per chunk the strap sends METADATA HISTORY_END with an 8-byte trim token
  5. we COMMIT the chunk to SQLite, THEN ack with HISTORICAL_DATA_RESULT (23)
  6. the strap trims the acked chunk and sends the next one, until HISTORY_COMPLETE

The ack is written only after the commit. A crash before the ack loses nothing:
the strap resends the chunk.
"""

from __future__ import annotations

import asyncio
import time

from bleak import BleakClient

from . import frame as fr
from . import uuids
from .ble import find_whoop
from .records import decode_v18, hist_version
from .store import Store, now_ms


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def run_sync(db: str, address: str | None, idle_s: float = 30.0, max_min: float = 30.0, deep: bool = True) -> int:
    store = Store(db)
    dev = await find_whoop(timeout=12.0, address=address)
    if dev is None:
        _log("strap not found")
        return 1
    loop = asyncio.get_running_loop()
    acks: asyncio.Queue[bytes] = asyncio.Queue()
    done = asyncio.Event()
    stats = {"frames": 0, "hist": 0, "decoded": 0, "chunks": 0, "bad": 0, "last": time.time(), "versions": {}}
    reasm: dict[str, fr.Reassembler] = {}

    async with BleakClient(dev, timeout=20.0) as client:
        session = store.start_session(dev.name, dev.address)
        _log(f"connected to {dev.name!r}; session {session}")
        await asyncio.wait_for(client.write_gatt_char(uuids.WHOOP5_CMD_WRITE, fr.CLIENT_HELLO, response=True), 30)

        def on_notify(sender, data: bytearray) -> None:
            key = str(getattr(sender, "uuid", sender)).lower()
            r = reasm.setdefault(key, fr.Reassembler())
            for raw in r.feed(bytes(data)):
                f = fr.parse_frame(raw)
                if f is None:
                    continue
                stats["frames"] += 1
                stats["last"] = time.time()
                store.add_frame_blob(session, now_ms(), int(key[4:8], 16), f.type, f.crc_ok, raw)
                if not f.crc_ok:
                    stats["bad"] += 1
                    continue
                if f.type == fr.PacketType.HISTORICAL_DATA:
                    stats["hist"] += 1
                    v = hist_version(raw)
                    stats["versions"][v] = stats["versions"].get(v, 0) + 1
                    store.bump_hist_count(session, v)
                    rec = decode_v18(raw)
                    if rec:
                        store.add_hist18(rec)
                        stats["decoded"] += 1
                elif f.type in (fr.PacketType.METADATA, fr.PacketType.PUFFIN_METADATA):
                    token = fr.history_end_data(raw)
                    if token is not None:
                        loop.call_soon_threadsafe(acks.put_nowait, token)
                    elif raw[fr.META_TYPE_OFF] == fr.MetaType.HISTORY_COMPLETE:
                        loop.call_soon_threadsafe(done.set)
                elif f.type == fr.PacketType.CONSOLE_LOGS:
                    text = bytes(c for c in f.payload if 32 <= c < 127).decode("ascii", "replace").strip()
                    if text:
                        print(f"   strap: {text[:110]}", flush=True)

        for u in uuids.WHOOP5_NOTIFY:
            await client.start_notify(u, on_notify)
        await client.write_gatt_char(uuids.WHOOP5_CMD_WRITE, fr.CLIENT_HELLO, response=True)
        await asyncio.sleep(1.0)

        # Write the R22 deep-stream flags before every drain, as the official app
        # and OpenStrap edge do: the flags must land before SEND_HISTORICAL_DATA.
        if deep:
            from .deep import ENABLE_SEQUENCE, flag_frame

            for i, (name, value) in enumerate(ENABLE_SEQUENCE):
                await client.write_gatt_char(uuids.WHOOP5_CMD_WRITE, flag_frame(name, value, 100 + i), response=True)
                await asyncio.sleep(0.05)
            _log(f"wrote {len(ENABLE_SEQUENCE)} deep-stream flags")
        seq = 10
        await client.write_gatt_char(
            uuids.WHOOP5_CMD_WRITE, fr.build_command(fr.Command.SEND_HISTORICAL_DATA, seq=seq, payload=b"\x00"), response=True
        )
        _log("history requested")
        start = time.time()
        while not done.is_set():
            if time.time() - stats["last"] > idle_s:
                _log(f"idle {idle_s:.0f}s, stopping")
                break
            if time.time() - start > max_min * 60:
                _log("time limit reached, stopping")
                break
            try:
                token = await asyncio.wait_for(acks.get(), 1.0)
            except asyncio.TimeoutError:
                continue
            store.commit()  # durable BEFORE the strap trims this chunk
            seq = (seq + 1) & 0xFF
            await client.write_gatt_char(uuids.WHOOP5_CMD_WRITE, fr.build_history_ack(token, seq=seq), response=True)
            stats["chunks"] += 1
            if stats["chunks"] % 10 == 0:
                _log(f"chunks {stats['chunks']}  records {stats['hist']}  decoded {stats['decoded']}")
        store.commit()

    store.close()
    _log(
        f"done. frames {stats['frames']} (bad crc {stats['bad']}), history records {stats['hist']}, "
        f"decoded v18 {stats['decoded']}, chunks acked {stats['chunks']}, versions {stats['versions']}"
    )
    return 0
