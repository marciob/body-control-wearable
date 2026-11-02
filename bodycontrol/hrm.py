"""Parser for the standard BLE Heart Rate Measurement characteristic (0x2A37).

Layout (Bluetooth SIG, Heart Rate Service 1.0):
  flags u8
    bit 0    heart rate value is u16 (else u8)
    bit 1    sensor contact detected
    bit 2    sensor contact feature supported
    bit 3    energy expended field present (u16, kJ)
    bit 4    R-R interval fields present (u16 each, unit 1/1024 s)
  heart rate  u8 or u16 LE
  [energy]    u16 LE
  [rr ...]    u16 LE, repeated to the end of the packet
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HeartRateMeasurement:
    hr: int
    rr_ms: tuple[int, ...]
    sensor_contact: bool | None  # None when the strap does not report contact
    energy_kj: int | None


def parse_heart_rate(data: bytes) -> HeartRateMeasurement | None:
    """Parse one 0x2A37 notification. Return None if the packet is too short."""
    if len(data) < 2:
        return None
    flags = data[0]
    idx = 1

    if flags & 0x01:
        if len(data) < idx + 2:
            return None
        hr = data[idx] | (data[idx + 1] << 8)
        idx += 2
    else:
        hr = data[idx]
        idx += 1

    contact: bool | None = None
    if flags & 0x04:
        contact = bool(flags & 0x02)

    energy: int | None = None
    if flags & 0x08:
        if len(data) >= idx + 2:
            energy = data[idx] | (data[idx + 1] << 8)
        idx += 2

    rr: list[int] = []
    if flags & 0x10:
        while idx + 1 < len(data):
            raw = data[idx] | (data[idx + 1] << 8)
            rr.append(round(raw * 1000 / 1024))
            idx += 2

    return HeartRateMeasurement(hr=hr, rr_ms=tuple(rr), sensor_contact=contact, energy_kj=energy)
