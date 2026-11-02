import datetime as dt
import json

from bodycontrol.store import Store
from bodycontrol.zones import bounds, day_zones, run_zones, window_start, zone_of


def test_zone_boundaries_at_max_hr_183():
    assert bounds(183) == [(92, 109), (110, 128), (129, 146), (147, 164), (165, None)]
    assert [zone_of(h, 183) for h in (91, 92, 109, 110, 128, 129, 146, 147, 164, 165, 190)] == [
        0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5]


def test_day_zones_counts_seconds_per_local_day():
    noon = int(dt.datetime(2026, 1, 5, 12, 0).timestamp())
    rows = [(noon + i, 80) for i in range(60)]  # 1 min rest
    rows += [(noon + 60 + i, 100) for i in range(120)]  # 2 min zone 1
    rows += [(noon + 86400 + i, 150) for i in range(30)]  # next day, 30 s zone 4
    table = day_zones(rows, 183)
    assert list(table) == ["2026-01-05", "2026-01-06"]
    assert table["2026-01-05"] == [60, 120, 0, 0, 0, 0]
    assert table["2026-01-06"] == [0, 0, 0, 0, 30, 0]


def test_run_zones_reads_hist18_and_skips_missing_hr(tmp_path, capsys):
    db = str(tmp_path / "t.sqlite")
    st = Store(db)
    start = int(dt.datetime.now().timestamp()) - 3600
    for i in range(300):
        st.add_hist18({"record_index": i, "unix": start + i, "hr": 120, "rr_ms": "[]", "sleep_state": 0})
    st.add_hist18({"record_index": 300, "unix": start + 300, "hr": None, "rr_ms": "[]", "sleep_state": 0})
    st.close()
    assert run_zones(db, 183, days=2, as_json=True) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["worn_s"] == 300 and out["zone_s"] == [0, 0, 300, 0, 0, 0]
    assert out["data_end"] == start + 300  # the newest record, even one without HR
    assert run_zones(db, 183, days=2) == 0
    text = capsys.readouterr().out
    assert "Z2 110-128" in text
    row = [ln for ln in text.splitlines() if ln.startswith("20")][-1].split()
    assert row[4:] == ["5", "0", "0", "0", "5"]  # Z2..Z5, then esforço = Z2 to Z5, in minutes
    assert run_zones(db, 50, days=2) == 2


def test_window_covers_whole_local_days_today_included(tmp_path, capsys):
    now = dt.datetime(2026, 3, 10, 15, 30).astimezone()
    assert window_start(1, now) == dt.datetime(2026, 3, 10, 0, 0).astimezone()
    assert window_start(3, now) == dt.datetime(2026, 3, 8, 0, 0).astimezone()

    db = str(tmp_path / "t.sqlite")
    st = Store(db)
    today0 = dt.datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    kept = int((today0 - dt.timedelta(days=1)).timestamp()) + 30 * 60  # yesterday 00:30
    cut = int((today0 - dt.timedelta(days=2)).timestamp()) + 23 * 3600 + 30 * 60  # day before, 23:30
    st.add_hist18({"record_index": 1, "unix": kept, "hr": 100, "rr_ms": "[]", "sleep_state": 0})
    st.add_hist18({"record_index": 2, "unix": cut, "hr": 100, "rr_ms": "[]", "sleep_state": 0})
    st.close()
    run_zones(db, 183, days=2, as_json=True)
    dates = [json.loads(ln)["date"] for ln in capsys.readouterr().out.strip().splitlines()]
    assert dates == [(today0 - dt.timedelta(days=1)).strftime("%Y-%m-%d")]
