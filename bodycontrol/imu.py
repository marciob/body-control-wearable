"""Live 100 Hz six-axis IMU capture on WHOOP 5.0 (bounded session).

Hardware-verified sequence (NOOP docs/RAW_DATA_CAPTURE.md):
  start: START_RAW_DATA (81) [0x01], then TOGGLE_IMU_MODE (106) [0x01, 0x01]
  stop:  STOP_RAW_DATA (82) [0x01], then TOGGLE_IMU_MODE (106) [0x01, 0x00]
Without the R22 flags the strap does not store this stream. With them (deep.py) it stores
the same buffers as history v21, so the daemon does not stream it by default.
Revision 1 of START_RAW_DATA defaults to a full day, so the stop is sent
in a finally block, and `bodycontrol imu --stop` sends it on its own.

Every frame is stored as a raw BLOB (table frame) and goes to S3 with the
hourly archive. Buffers with the R21 layout (1,244 bytes) are counted and checked.
"""

from __future__ import annotations

import asyncio
import struct
import time

from bleak import BleakClient

from . import frame as fr
from . import uuids
from .ble import find_whoop
from .store import Store, now_ms

START_RAW_DATA = 81
STOP_RAW_DATA = 82
TOGGLE_IMU_MODE = 106
R21_LEN = 1244
ACC_SCALE_G = 1 / 4096  # NOOP convention; not independently proven for every configuration


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def decode_r21(raw: bytes) -> dict | None:
    """Counts, base time and the accelerometer magnitude of sample 0, for a sanity check."""
    if len(raw) != R21_LEN:
        return None
    na = raw[24] | (raw[25] << 8)
    ng = raw[630] | (raw[631] << 8)
    if na > 100 or ng > 100:
        return None
    unix = int.from_bytes(raw[15:19], "little")
    if na:
        ax, ay, az = (struct.unpack_from("<h", raw, base)[0] for base in (28, 228, 428))
        g0 = (ax * ax + ay * ay + az * az) ** 0.5 * ACC_SCALE_G
    else:
        g0 = None
    return {"unix": unix, "acc_n": na, "gyro_n": ng, "g0": g0}


async def _send(client: BleakClient, cmd: int, payload: bytes, seq: int) -> None:
    await client.write_gatt_char(uuids.WHOOP5_CMD_WRITE, fr.build_command(cmd, seq=seq, payload=payload), response=True)


async def run_imu(db: str, address: str | None, minutes: float, stop_only: bool = False) -> int:
    dev = await find_whoop(timeout=12.0, address=address)
    if dev is None:
        _log("strap not found")
        return 1
    store = Store(db)
    reasm: dict[str, fr.Reassembler] = {}
    stats = {"frames": 0, "bytes": 0, "r21": 0, "types": {}, "g0": []}

    async with BleakClient(dev, timeout=20.0) as client:
        session = store.start_session(dev.name, dev.address)
        _log(f"connected to {dev.name!r}; session {session}")
        await asyncio.wait_for(client.write_gatt_char(uuids.WHOOP5_CMD_WRITE, fr.CLIENT_HELLO, response=True), 120)

        def on_notify(sender, data: bytearray) -> None:
            key = str(getattr(sender, "uuid", sender)).lower()
            for raw in reasm.setdefault(key, fr.Reassembler()).feed(bytes(data)):
                f = fr.parse_frame(raw)
                if f is None:
                    continue
                store.add_frame_blob(session, now_ms(), int(key[4:8], 16), f.type, f.crc_ok, raw)
                stats["frames"] += 1
                stats["bytes"] += len(raw)
                k = (f.type, len(raw))
                stats["types"][k] = stats["types"].get(k, 0) + 1
                d = decode_r21(raw) if f.crc_ok else None
                if d:
                    stats["r21"] += 1
                    if d["g0"] is not None and len(stats["g0"]) < 2000:
                        stats["g0"].append(d["g0"])

        for u in uuids.WHOOP5_NOTIFY:
            await client.start_notify(u, on_notify)
        await client.write_gatt_char(uuids.WHOOP5_CMD_WRITE, fr.CLIENT_HELLO, response=True)
        await asyncio.sleep(1.0)

        try:
            if not stop_only:
                await _send(client, START_RAW_DATA, b"\x01", 60)
                await _send(client, TOGGLE_IMU_MODE, b"\x01\x01", 61)
                _log(f"IMU streaming for {minutes:g} min. Ctrl-C stops early.")
                end = time.time() + minutes * 60
                last = 0.0
                while time.time() < end:
                    await asyncio.sleep(1.0)
                    store.commit()
                    if time.time() - last >= 10:
                        last = time.time()
                        _log(f"frames {stats['frames']}  R21 buffers {stats['r21']}  {stats['bytes'] / 1024:.0f} KB")
        except (asyncio.CancelledError, KeyboardInterrupt):
            _log("stopping early")
        finally:
            try:
                await _send(client, STOP_RAW_DATA, b"\x01", 62)
                await _send(client, TOGGLE_IMU_MODE, b"\x01\x00", 63)
                _log("stop commands sent")
            except Exception as e:
                _log(f"STOP FAILED: {type(e).__name__}: {e}. Run `bodycontrol imu --stop` now.")
            await asyncio.sleep(1.0)
            store.commit()

    store.close()
    g = stats["g0"]
    gtxt = f", |g| median {sorted(g)[len(g) // 2]:.3f} g" if g else ""
    _log(f"done. frames {stats['frames']}, R21 buffers {stats['r21']}{gtxt}")
    top = sorted(stats["types"].items(), key=lambda kv: -kv[1])[:8]
    _log("frame (type, length): count  " + "  ".join(f"{k}:{v}" for k, v in top))
    return 0
