"""Bond probe for WHOOP 5.0: subscribe to the vendor channels and send CLIENT_HELLO.

Read-only. It sends only CLIENT_HELLO and GET_BATTERY_LEVEL.
It sends no history command, so the strap trims nothing.
"""

from __future__ import annotations

import asyncio
import time

from bleak import BleakClient

from . import frame as fr
from . import uuids
from .ble import find_whoop


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def describe(f: fr.Frame) -> str:
    base = f"{f.type_name:24s} seq {f.seq:3d} cmd {f.cmd:3d} len {len(f.raw):4d} crc {'ok' if f.crc_ok else 'BAD'}"
    if f.type == fr.PacketType.CONSOLE_LOGS:
        text = bytes(c for c in f.payload if 32 <= c < 127).decode("ascii", "replace").strip()
        return base + f"  log: {text[:120]!r}"
    if f.type in (fr.PacketType.COMMAND_RESPONSE, fr.PacketType.PUFFIN_COMMAND_RESPONSE) and len(f.payload) >= 2:
        # inner[2] = resp_cmd at frame[10], frame[11] = resp_seq, frame[12] = result
        result = f.raw[12] if len(f.raw) > 12 else None
        names = {0: "FAILURE", 1: "SUCCESS", 2: "PENDING", 3: "UNSUPPORTED"}
        return base + f"  answers cmd {f.raw[10]} result {names.get(result, result)}  body {f.raw[13:-4].hex()[:64]}"
    return base + f"  payload {f.payload.hex()[:64]}"


async def run_probe(address: str | None, listen_s: float = 15.0, attempts: int = 12, retry_s: float = 5.0) -> int:
    _log("looking for the strap ...")
    dev = await find_whoop(timeout=12.0, address=address)
    if dev is None:
        _log("strap not found. Quit the WHOOP app on the phone. Tap the strap until the LEDs flash blue.")
        return 1
    frames: list[fr.Frame] = []
    reasm: dict[str, fr.Reassembler] = {}

    def on_notify(sender, data: bytearray) -> None:
        key = str(getattr(sender, "uuid", sender)).lower()
        r = reasm.setdefault(key, fr.Reassembler())
        for raw in r.feed(bytes(data)):
            f = fr.parse_frame(raw)
            if f is None:
                continue
            frames.append(f)
            print(f"   <- {key[4:8]} {describe(f)}", flush=True)

    async with BleakClient(dev, timeout=20.0) as client:
        _log(f"connected to {dev.name!r} ({dev.address})")
        svc = client.services.get_service(uuids.WHOOP5_SERVICE)
        if svc is None:
            _log("vendor service fd4b0001 not present. Not a WHOOP 5.0 / MG?")
            return 2
        _log("vendor service found: " + ", ".join(c.uuid[4:8] + "[" + ",".join(c.properties) + "]" for c in svc.characteristics))

        # Step 1: confirmed write of CLIENT_HELLO. On Apple this triggers the just-works bond.
        _log("writing CLIENT_HELLO with response (this triggers the bond) ...")
        _log(">>> WATCH THE SCREEN: macOS may show a Bluetooth pairing window or notification. Click Connect / Pair. Waiting up to 120s.")
        for attempt in range(1, attempts + 1):
            try:
                # The first write can block while macOS waits for the user to accept a pairing window.
                await asyncio.wait_for(client.write_gatt_char(uuids.WHOOP5_CMD_WRITE, fr.CLIENT_HELLO, response=True), 120 if attempt == 1 else 30)
                _log(f"CLIENT_HELLO accepted on attempt {attempt}: link is bonded")
                break
            except Exception as e:
                _log(f"attempt {attempt}/{attempts}: {type(e).__name__}: {e}")
                if attempt == attempts:
                    _log("bond did not come up. Tap the top of the strap until the LEDs flash blue, then retry.")
                    return 3
                await asyncio.sleep(retry_s)

        # Step 2: subscribe the four vendor notify channels.
        for u in uuids.WHOOP5_NOTIFY:
            try:
                await asyncio.wait_for(client.start_notify(u, on_notify), 10)
                _log(f"subscribed {u[4:8]}")
            except Exception as e:
                _log(f"subscribe {u[4:8]} FAILED: {type(e).__name__}: {e}")

        # Step 3: send CLIENT_HELLO again now that we listen, then a harmless battery query.
        await client.write_gatt_char(uuids.WHOOP5_CMD_WRITE, fr.CLIENT_HELLO, response=True)
        await asyncio.sleep(1.0)
        batt = fr.build_command(fr.Command.GET_BATTERY_LEVEL, seq=2, payload=b"\x00")
        await client.write_gatt_char(uuids.WHOOP5_CMD_WRITE, batt, response=True)
        _log(f"listening {listen_s:.0f}s ...")
        await asyncio.sleep(listen_s)

    ok = sum(1 for f in frames if f.crc_ok)
    _log(f"done. {len(frames)} frames, {ok} CRC-valid.")
    if ok:
        _log("RESULT: bond + vendor session WORK on this Mac.")
        return 0
    _log("RESULT: no vendor frames. Bond or session did not come up.")
    return 4
