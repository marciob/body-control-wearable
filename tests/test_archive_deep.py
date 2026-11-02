from bodycontrol import frame as f
from bodycontrol.archive import pack, unpack
from bodycontrol.deep import ENABLE_SEQUENCE, flag_frame
from bodycontrol.records import decode_v18
from tests.test_records import make_v18


def test_archive_roundtrip():
    rows = [(1000, 3, 47, 1, b"\xaa\x01abc"), (2000, 5, 50, 0, bytes(300))]
    assert list(unpack(pack(rows))) == rows


def test_flag_frame_layout():
    fr_ = flag_frame("enable_r22_packets", 0x32, seq=7)
    p = f.parse_frame(fr_)
    assert p.crc_ok and p.cmd == 0x78 and p.seq == 7
    body = p.payload
    assert body[0] == 0x01 and len(body) == 41
    assert body[1:19] == b"enable_r22_packets" and body[19:33] == bytes(14)
    assert body[33] == 0x32 and body[34:] == bytes(7)
    assert len(ENABLE_SEQUENCE) == 16


def test_v18_all_fields():
    r = decode_v18(make_v18())
    assert r["hr"] == 58 and r["sleep_state"] == 2 and "temp_aux1_c" in r and "quality_f32" in r


def test_decode_r21():
    import struct

    from bodycontrol.imu import R21_LEN, decode_r21

    raw = bytearray(R21_LEN)
    raw[15:19] = (1760000000).to_bytes(4, "little")
    raw[24] = 100
    raw[630] = 100
    struct.pack_into("<h", raw, 428, 4096)  # az = 1 g
    d = decode_r21(bytes(raw))
    assert d["acc_n"] == 100 and d["gyro_n"] == 100 and abs(d["g0"] - 1.0) < 1e-9
    assert decode_r21(bytes(10)) is None


def test_archive_stops_at_frames_present_at_start(tmp_path, monkeypatch):
    from bodycontrol import archive
    from bodycontrol.store import Store

    db = str(tmp_path / "t.sqlite")
    st = Store(db)
    for i in range(10):
        st.add_frame_blob(1, 1000 + i, 1, 47, True, b"\x01\x02")
    st.commit()
    uploads = []

    def fake_run(cmd, **kw):
        # The daemon writes a new frame during every upload.
        uploads.append(cmd)
        st.add_frame_blob(1, 9999, 1, 47, True, b"\x03")
        st.commit()
        return subprocess.CompletedProcess(cmd, 0, "", "")

    import subprocess

    monkeypatch.setattr(archive.subprocess, "run", fake_run)
    assert archive.run_archive(db, "t", "b", "p", batch=4) == 0
    assert len(uploads) == 3  # 4 + 4 + 2 frames, then stop
    (left,) = st.conn.execute("SELECT COUNT(*) FROM frame").fetchone()
    assert left == 3  # only the frames written during the uploads stay local
    st.close()


def test_archive_key_unique_when_ids_restart():
    from bodycontrol.archive import archive_key

    # Same id range on the same day, uploaded at different times: the keys must differ.
    a = archive_key("mac-auto", 1760918400000, 1, 500)
    b = archive_key("mac-auto", 1760940000000, 1, 500)
    assert a != b
    assert a == "raw/2025/10/20/mac-auto-1760918400000-0000000001-0000000500.bcf.xz"
