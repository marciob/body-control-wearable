"""Decoders for the WHOOP 5.0 deep historical records (type 47 versions 20, 21, 26).

The strap sends them only after the R22 feature flags are set (see deep.py).
Layouts follow the OpenStrap protocol notes (gen5 records) and
NOOP docs/PROTOCOL_SENSORS.md. Offsets below are INNER offsets: inner[i] == frame[i + 8].

Shared header (inner):
  [0] type 0x2F  [1] version  [2] flags (bit 7: optical at 25 Hz, else 50 Hz)
  [3:7] record_index u32  [7:11] unix u32  [11:13] sub-second u16 (1/32768 s)
"""

from __future__ import annotations

import struct

INNER_OFF = 8
V20_INNER_LEN = 2128  # frame 2140
V21_INNER_LEN = 1232  # frame 1244
V26_INNER_LEN = 76  # frame 88
OPTICAL_CHANNELS = ("green", "red", "fourth", "ir", "ambient")  # v20 block order; block 3 may be a fallback (flags bit 0)


def inner_of(frame: bytes) -> bytes:
    return frame[INNER_OFF:-4]


def header(inner: bytes) -> dict:
    return {
        "version": inner[1],
        "flags": inner[2],
        "record_index": int.from_bytes(inner[3:7], "little"),
        "unix": int.from_bytes(inner[7:11], "little"),
        "subsec": (inner[11] | (inner[12] << 8)) / 32768.0,
        "ppg_hz": 25 if inner[2] & 0x80 else 50,
    }


def _sx20(v: int) -> int:
    v &= 0xFFFFF
    return v - 0x100000 if v & 0x80000 else v


def decode_v20(frame: bytes) -> dict | None:
    """Raw optical buffer: 5 channel blocks, each with 2 photodiode slots of up to 50 samples."""
    inner = inner_of(frame)
    if len(inner) != V20_INNER_LEN or inner[1] != 20:
        return None
    out = header(inner)
    out["sample_rate_hz"] = inner[15] | (inner[16] << 8)
    blocks = []
    for b in range(5):
        start = 18 + b * 422
        n = inner[start]
        n = 0 if n > 50 else n
        ch = []
        for slot in (start + 21, start + 221):
            ch.append([_sx20(int.from_bytes(inner[slot + 4 * i : slot + 4 * i + 4], "little")) for i in range(n)])
        meta = inner[start + 1 : start + 21]
        blocks.append(
            {
                "channel": OPTICAL_CHANNELS[b],
                "n": n,
                "pd_a": ch[0],
                "pd_b": ch[1],
                "led_a_current_ua": (meta[1] | (meta[2] << 8)) * 10,
                "led_b_current_ua": (meta[4] | (meta[5] << 8)) * 10,
                "pd_a_source": meta[6],
                "pd_a_range_ua": int.from_bytes(meta[7:11], "little"),
                "pd_a_offset_na": struct.unpack_from("<h", meta, 11)[0] * 10,
                "pd_b_source": meta[13],
                "pd_b_range_ua": int.from_bytes(meta[14:18], "little"),
                "pd_b_offset_na": struct.unpack_from("<h", meta, 18)[0] * 10,
            }
        )
    out["blocks"] = blocks
    return out


def decode_imu_buffer(frame: bytes) -> dict | None:
    """100 Hz six-axis buffer, R21 layout: history v21 and the live type-43 stream (1,244-byte frame)."""
    if len(frame) != V21_INNER_LEN + 12:
        return None
    f = frame
    na = f[24] | (f[25] << 8)
    ng = f[630] | (f[631] << 8)
    if na > 100 or ng > 100:
        return None

    def col(base: int, n: int) -> list[int]:
        return list(struct.unpack_from(f"<{n}h", f, base)) if n else []

    return {
        "type": f[8],
        "version": f[9],
        "record_index": int.from_bytes(f[11:15], "little"),
        "unix": int.from_bytes(f[15:19], "little"),
        "acc_n": na,
        "gyro_n": ng,
        "ax": col(28, na), "ay": col(228, na), "az": col(428, na),
        "gx": col(640, ng), "gy": col(840, ng), "gz": col(1040, ng),
        # NOOP convention, not independently proven for every configuration:
        "acc_scale_g": 1 / 4096,
        "gyro_scale_dps": 2000 / 32768,
    }


def decode_v26(frame: bytes) -> dict | None:
    """PPG window: one absolute 20-bit sample + 24 i16 deltas = 25 samples.

    A delta of exactly -32768 or 32767 is a saturated step; that sample and all
    later ones in the window are ambiguous ('trusted' counts the clean prefix).
    """
    inner = inner_of(frame)
    if len(inner) != V26_INNER_LEN or inner[1] != 26:
        return None
    out = header(inner)
    first = struct.unpack_from("<i", inner, 15)[0]
    deltas = list(struct.unpack_from("<24h", inner, 19))
    samples = [first]
    trusted = None
    for i, d in enumerate(deltas):
        if trusted is None and d in (-32768, 32767):
            trusted = i + 1
        samples.append(samples[-1] + d)
    accel = struct.unpack_from("<f", inner, 67)[0]
    out.update(
        {
            "pip_state_counter": inner[13] | (inner[14] << 8),
            "samples": samples,
            "trusted": trusted if trusted is not None else len(samples),
            "out_of_range": [i for i, s in enumerate(samples) if not -524288 <= s <= 524287],
            "accel_delta_g": accel if accel == accel else None,
            "channel_state_word": inner[71] | (inner[72] << 8),
        }
    )
    return out


def decode_deep(frame: bytes) -> dict | None:
    """Dispatch on frame length + version byte. Returns None for anything else."""
    if len(frame) < 12:
        return None
    v = frame[9]
    if v == 20:
        return decode_v20(frame)
    if v == 21 or (frame[8] == 43 and len(frame) == V21_INNER_LEN + 12):
        return decode_imu_buffer(frame)
    if v == 26:
        return decode_v26(frame)
    return None
