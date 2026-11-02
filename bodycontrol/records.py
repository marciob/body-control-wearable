"""Decoder for WHOOP 5.0 historical records (packet type 47).

Only layout version 18 (124 bytes) is decoded into columns. Offsets are absolute
frame offsets from the hardware-verified map in NOOP docs/BLE_REVERSE_ENGINEERING.md.
Every other version (20, 21, 26, ...) is kept as a raw frame and archived to S3.
Field names that NOOP marks as unconfirmed keep a neutral name here.
"""

from __future__ import annotations

import json
import struct

from .frame import INNER_OFF, PacketType


def _f32(b: bytes, off: int) -> float:
    return struct.unpack_from("<f", b, off)[0]


def _u16(b: bytes, off: int) -> int:
    return b[off] | (b[off + 1] << 8)


def _i16(b: bytes, off: int) -> int:
    return struct.unpack_from("<h", b, off)[0]


def hist_version(raw: bytes) -> int | None:
    if len(raw) < 12 or raw[INNER_OFF] != PacketType.HISTORICAL_DATA:
        return None
    return raw[9]


def decode_v18(raw: bytes) -> dict | None:
    """All known v18 fields, as stored in table hist18."""
    if hist_version(raw) != 18 or len(raw) < 117:
        return None
    rr_count = raw[23]
    # Four u16 slots at 24..31. Bytes 32..35 hold other fields (cardiac_flags at 33),
    # so a larger count must not read past slot 4. Zero slots are empty.
    rr = [v for v in (_u16(raw, 24 + 2 * i) for i in range(min(rr_count, 4))) if v]
    dyn = _f32(raw, 41)
    skin = _u16(raw, 73) / 100.0
    q = _f32(raw, 113)
    return {
        "record_index": int.from_bytes(raw[11:15], "little"),
        "unix": int.from_bytes(raw[15:19], "little"),
        "hr": raw[22],
        "rr_ms": json.dumps(rr),
        "hr_quality_flags": raw[36],
        "cardiac_flags": raw[33],
        "cardiac_status": raw[40],
        "dyn_acc_g": dyn if 0.0 <= dyn <= 8.0 else None,
        "grav_x": _f32(raw, 45),
        "grav_y": _f32(raw, 49),
        "grav_z": _f32(raw, 53),
        "step_counter": _u16(raw, 57),
        "step_cadence": raw[59],
        "motion_wear": raw[63],
        "temp_aux1_c": _i16(raw, 69) / 10.0,
        "temp_aux2_c": _i16(raw, 71) / 10.0,
        "skin_temp_c": skin if 20.0 <= skin <= 45.0 else None,
        "status_word": _u16(raw, 75),
        "sleep_state": (raw[81] >> 4) & 3,
        "sleep_flags": raw[81] & 0x0F,
        "spo2_candidate": raw[82],
        "optical_base_a": raw[106],
        "optical_base_b": raw[107],
        "optical_amp_a": raw[108],
        "optical_amp_b": raw[109],
        "quality_f32": q if q == q and abs(q) < 1e6 else None,
    }


def decode_hist(raw: bytes) -> dict | None:
    """Legacy row shape for table hist_record (kept for old databases)."""
    r = decode_v18(raw)
    if r is None:
        return None
    return {
        "record_index": r["record_index"],
        "unix": r["unix"],
        "version": 18,
        "hr": r["hr"],
        "rr_ms": r["rr_ms"],
        "dyn_acc_g": r["dyn_acc_g"],
        "grav_x": r["grav_x"],
        "grav_y": r["grav_y"],
        "grav_z": r["grav_z"],
        "skin_temp_c": r["skin_temp_c"],
        "sleep_state": r["sleep_state"],
        "hex": raw.hex(),
    }
