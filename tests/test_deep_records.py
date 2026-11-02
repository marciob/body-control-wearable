from bodycontrol import frame as f
from bodycontrol.deep_records import decode_deep, decode_v26

# Real WHOOP 5.0 v26 record from the author's own strap (2026-11-02).
V26_HEX = (
    "aa015000010035412f1a803598800044e2e86a0a7713004f9f040085ffedff0600bdffc3ffc9ffe7ff0d00570056"
    "009d009b00b200ca00ce00d4009800edfec7fdcefd3ffeb9fe50ffd2ff0099083c500b000100910d1a7c"
)
V26_DELTAS_HEAD = [-123, -19, 6, -67, -61, -55, -25, 13]


def test_real_v26_fixture():
    fr_ = bytes.fromhex(V26_HEX)
    assert f.verify_frame(fr_)
    r = decode_v26(fr_)
    assert r["version"] == 26
    assert r["record_index"] == 8427573
    assert r["unix"] == 1793647172
    assert r["pip_state_counter"] == 19
    assert len(r["samples"]) == 25
    deltas = [b - a for a, b in zip(r["samples"], r["samples"][1:])]
    assert deltas[:8] == V26_DELTAS_HEAD
    assert decode_deep(fr_)["samples"] == r["samples"]


def test_imu_buffer_dispatch():
    import struct

    raw = bytearray(1244)
    raw[8], raw[9] = 43, 0
    raw[24], raw[630] = 2, 1
    struct.pack_into("<2h", raw, 428, 4096, 4000)
    r = decode_deep(bytes(raw))
    assert r["az"] == [4096, 4000] and r["gyro_n"] == 1 and len(r["gx"]) == 1
