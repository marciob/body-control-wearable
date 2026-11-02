import math

from bodycontrol.hrv import RollingHRV


def test_rmssd_and_sdnn_known_values():
    h = RollingHRV(window_s=600, min_count=5)
    rrs = [1000, 1020, 980, 1010, 990, 1000]
    t = 0.0
    for rr in rrs:
        t += rr / 1000
        assert h.add(t, rr)
    diffs = [b - a for a, b in zip(rrs, rrs[1:])]
    assert math.isclose(h.rmssd(), math.sqrt(sum(d * d for d in diffs) / len(diffs)))
    mean = sum(rrs) / len(rrs)
    assert math.isclose(h.sdnn(), math.sqrt(sum((x - mean) ** 2 for x in rrs) / (len(rrs) - 1)))
    assert math.isclose(h.mean_hr(), 60000 * len(rrs) / sum(rrs))


def test_needs_min_count():
    h = RollingHRV(min_count=3)
    h.add(1.0, 1000)
    h.add(2.0, 1000)
    assert h.rmssd() is None
    h.add(3.0, 1000)
    assert h.rmssd() == 0.0


def test_rejects_out_of_range_and_jumps():
    h = RollingHRV()
    assert h.add(1.0, 1000)
    assert not h.add(2.0, 250)  # below min_rr
    assert not h.add(3.0, 2500)  # above max_rr
    assert h.add(4.0, 1000)
    assert not h.add(5.0, 1300)  # +30% jump vs previous raw
    assert h.add(6.3, 1310)  # +0.8% vs previous raw: accepted again
    assert h.rejected == 3
    assert h.accepted == 3


def test_window_drops_old():
    h = RollingHRV(window_s=10, min_count=1)
    h.add(0.0, 1000)
    h.add(5.0, 1000)
    assert h.count == 2
    h.add(20.0, 1000)
    assert h.count == 1
