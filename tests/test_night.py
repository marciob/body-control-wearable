import json
import math

from bodycontrol.night import clean_runs, find_nights, night_summary, rmssd, run_night
from bodycontrol.store import Store


def test_malik_rule_rejects_outlier_and_breaks_the_run():
    seq = [1000] * 15 + [1500] + [1200] * 15
    runs = clean_runs(seq)
    assert [len(r) for r in runs] == [15, 15]
    # No difference is taken across the rejected beat: a spliced 1000->1200 step would give 200 ms.
    assert rmssd(runs) == 0.0


def test_rmssd_needs_enough_differences():
    assert rmssd([[1000, 1040] * 5]) is None  # 9 differences
    v = rmssd([[1000, 1040] * 11])  # 21 differences of 40 ms
    assert v is not None and abs(v - 40.0) < 1e-9


def test_rr_out_of_range_rejected():
    assert clean_runs([250, 1000, 1000, 2100]) == [[1000, 1000]]


def _asleep(start, seconds, hr=60, rr=(1000,)):
    return [(start + i, hr, json.dumps(list(rr)), 2) for i in range(seconds)]


def test_find_nights_merges_short_wake_and_drops_short_sleep():
    rows = _asleep(1_000_000, 2 * 3600)  # 2 h asleep
    rows += [(1_000_000 + 2 * 3600 + i, 70, "[]", 0) for i in range(20 * 60)]  # 20 min awake
    rows += _asleep(1_000_000 + 2 * 3600 + 20 * 60, 2 * 3600)  # 2 h asleep again
    rows += _asleep(1_000_000 + 10 * 3600, 3600)  # a 1 h nap, 6 h later
    nights = find_nights(rows)
    assert len(nights) == 1
    assert len(nights[0]) == 4 * 3600


def test_night_summary_rhr_is_the_median_and_hrv_is_rmssd():
    start = 1_000_000
    rows = _asleep(start, 3 * 3600, hr=65, rr=(1000,))  # 3 h at 65 bpm, flat R-R
    rows += _asleep(start + 3 * 3600, 3600, hr=55, rr=(1020, 980))  # 1 h at 55 bpm, +-20 ms
    n = night_summary([(u, h, r) for u, h, r, _ in rows])
    assert n["asleep_s"] == 4 * 3600
    assert n["rhr"] == 65  # median, not the mean (62.5) and not the lowest window (55)
    assert n["windows"] == 48
    # 36 flat windows (RMSSD 0) and 12 windows of 40 ms steps: mean 10, median 0
    assert abs(n["hrv"] - 10.0) < 1e-6
    assert n["hrv_median"] == 0.0
    assert n["rejected"] == 0


def test_run_night_reads_hist18(tmp_path, capsys):
    db = str(tmp_path / "t.sqlite")
    st = Store(db)
    import time

    start = int(time.time()) - 8 * 3600
    for i, (u, h, r, s) in enumerate(_asleep(start, 4 * 3600, hr=62, rr=(950, 970))):
        st.add_hist18({"record_index": i, "unix": u, "hr": h, "rr_ms": r, "sleep_state": s})
    st.close()
    assert run_night(db, days=2, as_json=True) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["rhr"] == 62 and abs(out["hrv"] - 20.0) < 1e-6 and out["asleep_s"] == 4 * 3600
    assert not math.isnan(out["hrv"])


def test_is_complete_needs_records_after_the_night():
    from bodycontrol.night import TAIL_S, is_complete

    assert is_complete(1000, 1000 + TAIL_S)
    assert not is_complete(1000, 1000 + TAIL_S - 1)
    assert not is_complete(1000, 1000)


def test_run_night_marks_a_night_whose_records_stop_asleep(tmp_path, capsys):
    import time

    db = str(tmp_path / "t.sqlite")
    st = Store(db)
    start = int(time.time()) - 8 * 3600
    rows = _asleep(start, 4 * 3600, hr=62, rr=(950, 970))
    for i, (u, h, r, s) in enumerate(rows):
        st.add_hist18({"record_index": i, "unix": u, "hr": h, "rr_ms": r, "sleep_state": s})
    st.close()

    run_night(db, days=2, as_json=True)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["complete"] is False and out["data_end"] == rows[-1][0]
    run_night(db, days=2)
    text = capsys.readouterr().out
    assert "incomplete: data ends" in text and "-?" in text

    st = Store(db)  # 10 min awake after the night: now it is complete
    for j in range(600):
        st.add_hist18({"record_index": len(rows) + j, "unix": rows[-1][0] + 1 + j, "hr": 70, "rr_ms": "[]", "sleep_state": 0})
    st.close()
    run_night(db, days=2, as_json=True)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["complete"] is True and out["asleep_s"] == 4 * 3600
    run_night(db, days=2)
    assert "incomplete" not in capsys.readouterr().out
