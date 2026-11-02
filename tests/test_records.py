import json
import struct

from bodycontrol import frame as f
from bodycontrol.records import decode_hist, decode_v18


def make_v18(record_index=4242, unix=1760000000, hr=58, rr=(1030, 1010), skin=3412):
    raw = bytearray(124)
    raw[0:2] = b"\xaa\x01"
    raw[8] = f.PacketType.HISTORICAL_DATA
    raw[9] = 18
    raw[11:15] = record_index.to_bytes(4, "little")
    raw[15:19] = unix.to_bytes(4, "little")
    raw[22] = hr
    raw[23] = len(rr)
    for i, v in enumerate(rr):
        raw[24 + 2 * i : 26 + 2 * i] = v.to_bytes(2, "little")
    struct.pack_into("<f", raw, 41, 0.01)
    struct.pack_into("<fff", raw, 45, 0.0, 0.0, 1.0)
    raw[73:75] = skin.to_bytes(2, "little")
    raw[81] = 2 << 4
    return bytes(raw)


def test_decode_v18():
    r = decode_hist(make_v18())
    assert r["record_index"] == 4242
    assert r["unix"] == 1760000000
    assert r["hr"] == 58
    assert json.loads(r["rr_ms"]) == [1030, 1010]
    assert abs(r["skin_temp_c"] - 34.12) < 1e-9
    assert abs(r["grav_z"] - 1.0) < 1e-6
    assert r["sleep_state"] == 2


def test_other_versions_not_decoded():
    raw = bytearray(make_v18())
    raw[9] = 26
    assert decode_hist(bytes(raw)) is None


def test_v18_reads_at_most_four_rr_slots():
    raw = bytearray(make_v18(rr=(1000, 1010, 1020, 1030)))
    raw[23] = 6  # count says six, but only four slots exist
    raw[33] = 0x55  # cardiac_flags: must not be read as part of an R-R value
    r = decode_v18(bytes(raw))
    assert json.loads(r["rr_ms"]) == [1000, 1010, 1020, 1030]
    assert r["cardiac_flags"] == 0x55
