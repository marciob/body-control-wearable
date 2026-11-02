from bodycontrol import frame as f


def test_client_hello_golden():
    built = f.build_command(f.CLIENT_HELLO_CMD, seq=1, payload=b"\x01")
    assert built == f.CLIENT_HELLO
    assert f.verify_frame(f.CLIENT_HELLO)


def test_parse_client_hello():
    p = f.parse_frame(f.CLIENT_HELLO)
    assert p is not None
    assert p.type == f.PacketType.COMMAND
    assert p.seq == 1
    assert p.cmd == 145
    assert p.payload == b"\x01"
    assert p.crc_ok


def test_corrupt_crc_rejected():
    bad = bytearray(f.CLIENT_HELLO)
    bad[-1] ^= 0xFF
    assert not f.verify_frame(bytes(bad))
    assert f.parse_frame(bytes(bad)).crc_ok is False
    bad_head = bytearray(f.CLIENT_HELLO)
    bad_head[6] ^= 0x01
    assert not f.verify_frame(bytes(bad_head))


def test_roundtrip_payload():
    fr = f.build_command(f.Command.GET_BATTERY_LEVEL, seq=9, payload=b"\x00")
    p = f.parse_frame(fr)
    assert p.cmd == 26 and p.seq == 9 and p.payload == b"\x00" and p.crc_ok


def test_reassembler_splits_and_resyncs():
    a = f.build_command(1, seq=1, payload=b"\x00")
    b = f.build_command(2, seq=2, payload=bytes(range(20)))
    stream = b"\x00\x11" + a + b  # leading junk
    r = f.Reassembler()
    out = []
    for i in range(0, len(stream), 5):
        out += r.feed(stream[i : i + 5])
    assert out == [a, b]
    assert r.buf == bytearray()


def test_history_end_data_and_ack():
    token = bytes(range(8))
    # METADATA frame: inner = type 49, seq, cmd(meta type at frame[10]) ... trim token at frame[21:29]
    inner = bytes([f.PacketType.METADATA, 0x05, f.MetaType.HISTORY_END]) + bytes(10) + token + bytes(4)
    decl = len(inner) + 4
    head = bytes([0xAA, 0x01, decl & 0xFF, decl >> 8, 0x00, 0x01])
    fr = head + f.crc16_modbus(head).to_bytes(2, "little") + inner + f.crc32(inner).to_bytes(4, "little")
    assert f.verify_frame(fr)
    assert f.history_end_data(fr) == token
    ack = f.build_history_ack(token, seq=3)
    p = f.parse_frame(ack)
    assert p.cmd == f.Command.HISTORICAL_DATA_RESULT and p.payload == b"\x01" + token


def test_inner_is_padded_to_4():
    fr_ = f.build_command(106, seq=1, payload=b"\x01\x01")
    p = f.parse_frame(fr_)
    assert p.crc_ok and p.payload == b"\x01\x01\x00\x00\x00"
    assert (len(fr_) - 12) % 4 == 0
