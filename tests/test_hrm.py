from bodycontrol.hrm import parse_heart_rate


def test_u8_hr_with_rr_and_contact():
    # flags: u8 HR, contact detected+supported, RR present = 0b0001_0110
    rr1 = round(0.968 * 1024)
    rr2 = round(0.952 * 1024)
    pkt = bytes([0x16, 62]) + rr1.to_bytes(2, "little") + rr2.to_bytes(2, "little")
    m = parse_heart_rate(pkt)
    assert m is not None
    assert m.hr == 62
    assert m.sensor_contact is True
    assert m.energy_kj is None
    assert m.rr_ms == (968, 952)


def test_u16_hr_energy_no_rr():
    # flags: u16 HR, contact supported but not detected, energy present = 0b0000_1101
    pkt = bytes([0x0D]) + (300).to_bytes(2, "little") + (1234).to_bytes(2, "little")
    m = parse_heart_rate(pkt)
    assert m is not None
    assert m.hr == 300
    assert m.sensor_contact is False
    assert m.energy_kj == 1234
    assert m.rr_ms == ()


def test_no_contact_feature():
    m = parse_heart_rate(bytes([0x00, 70]))
    assert m is not None
    assert m.hr == 70
    assert m.sensor_contact is None


def test_too_short():
    assert parse_heart_rate(b"") is None
    assert parse_heart_rate(b"\x00") is None
    assert parse_heart_rate(b"\x01\x05") is None
