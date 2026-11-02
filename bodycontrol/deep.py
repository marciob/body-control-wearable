"""Enable the WHOOP 5.0 deep data streams ("R22" feature flags).

The official app writes these 16 persistent feature flags on every connect.
Until they are set, the strap emits only the basic v18 record. Each flag is one
SET_FF_VALUE (cmd 0x78) command: payload 0x01 + flag name NUL-padded to 32 bytes
+ one ASCII value byte + 7 zero bytes. Write with response, about 80 ms apart,
with the strap on the wrist. Sequence from NOOP Whoop5Config.swift, validated
against judes.club and real HCI captures of the official app.
"""

from __future__ import annotations

import asyncio
import time

from bleak import BleakClient

from . import frame as fr
from . import uuids
from .ble import find_whoop

SET_FF_VALUE = 0x78

ENABLE_SEQUENCE: tuple[tuple[str, int], ...] = (
    ("enable_r22_packets", 0x32),
    ("enable_r22_v2_packets", 0x32),
    ("enable_r22_v3_packets", 0x32),
    ("enable_r22_v4_packets", 0x31),
    ("enable_r22_v5_packets", 0x32),
    ("enable_r22_v6_packets", 0x32),
    ("enable_r22_v8_packets", 0x32),
    ("make_hrfm_visible", 0x32),
    ("disable_pip_r26_packets", 0x32),
    ("wear_detect_bias", 0x32),
    ("hr_ch_switching", 0x32),
    ("ir_hw_switching", 0x32),
    ("enable_passive_strap_fit_gen5", 0x31),
    ("enable_sig11_during_sleep", 0x32),
    ("dorset_inhibit_wpt", 0x32),
    # Two night captures of the official app write '2' (wearable5.0; NOOP #103); one capture during a
    # workout writes '1' (NOOP #423). Effect unknown.
    ("enable_sig12", 0x32),
)


def flag_body(name: str, value: int) -> bytes:
    p = bytearray(40)
    b = name.encode("ascii")[:32]
    p[: len(b)] = b
    p[32] = value
    return bytes(p)


def flag_frame(name: str, value: int, seq: int) -> bytes:
    return fr.build_command(SET_FF_VALUE, seq=seq, payload=b"\x01" + flag_body(name, value))


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def run_enable(address: str | None) -> int:
    dev = await find_whoop(timeout=12.0, address=address)
    if dev is None:
        _log("strap not found")
        return 1
    results: dict[int, int] = {}
    seen: list[tuple[int, int, int]] = []
    names = {0: "FAILURE", 1: "SUCCESS", 2: "PENDING", 3: "UNSUPPORTED"}
    reasm: dict[str, fr.Reassembler] = {}

    def on_notify(sender, data: bytearray) -> None:
        key = str(getattr(sender, "uuid", sender)).lower()
        for raw in reasm.setdefault(key, fr.Reassembler()).feed(bytes(data)):
            f = fr.parse_frame(raw)
            if f and f.crc_ok and f.type in (fr.PacketType.COMMAND_RESPONSE, fr.PacketType.PUFFIN_COMMAND_RESPONSE):
                if len(raw) > 12:
                    seen.append((raw[9], raw[10], raw[12]))
                if raw[10] == SET_FF_VALUE and len(raw) > 12:
                    # frame[9] is the strap's own response counter, not our seq.
                    # Replies arrive in write order, so match them by position.
                    results[len(results)] = raw[12]

    async with BleakClient(dev, timeout=20.0) as client:
        _log(f"connected to {dev.name!r}")
        await asyncio.wait_for(client.write_gatt_char(uuids.WHOOP5_CMD_WRITE, fr.CLIENT_HELLO, response=True), 120)
        for u in uuids.WHOOP5_NOTIFY:
            await client.start_notify(u, on_notify)
        await client.write_gatt_char(uuids.WHOOP5_CMD_WRITE, fr.CLIENT_HELLO, response=True)
        await asyncio.sleep(1.0)
        first_seq = 40
        for i, (name, value) in enumerate(ENABLE_SEQUENCE):
            await client.write_gatt_char(uuids.WHOOP5_CMD_WRITE, flag_frame(name, value, first_seq + i), response=True)
            await asyncio.sleep(0.25)
        deadline = time.time() + 15
        while time.time() < deadline and len(results) < len(ENABLE_SEQUENCE):
            await asyncio.sleep(0.5)
        _log(f"{len(seen)} command responses seen")

    ok = 0
    for i, (name, value) in enumerate(ENABLE_SEQUENCE):
        r = results.get(i)
        ok += r == 1
        _log(f"{name:32s} = '{chr(value)}'  -> {names.get(r, 'no reply') if r is not None else 'no reply'}")
    _log(f"{ok}/{len(ENABLE_SEQUENCE)} flags acknowledged SUCCESS")
    return 0 if ok == len(ENABLE_SEQUENCE) else 2
