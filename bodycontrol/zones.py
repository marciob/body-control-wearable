"""Minutes in heart-rate zones per day, from the 1 Hz history (table hist18).

Zones follow WHOOP: zone k covers [50 + 10 (k - 1), 50 + 10 k) % of the maximum heart
rate, k = 1..5. At or above 100 % still counts as zone 5. Below 50 % is rest, not a
zone. WHOOP sets the maximum to 220 - age unless the user edits it in the app.
Each record is one second worn, so seconds in a zone are the count of records whose
heart rate falls in it. Days are local calendar days.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3

LOWER = (0.5, 0.6, 0.7, 0.8, 0.9)


def zone_of(hr: int, max_hr: int) -> int:
    """0 = below 50 % of max_hr (rest), 1..5 = WHOOP zones."""
    frac = hr / max_hr
    z = 0
    for k, lo in enumerate(LOWER, 1):
        if frac >= lo:
            z = k
    return z


def bounds(max_hr: int) -> list[tuple[int, int | None]]:
    """Lowest and highest whole bpm of each zone 1..5; zone 5 has no upper bound."""
    starts = [math.ceil(lo * max_hr) for lo in LOWER]
    return [(s, starts[i + 1] - 1 if i + 1 < len(starts) else None) for i, s in enumerate(starts)]


def day_zones(rows: list[tuple[int, int]], max_hr: int) -> dict[str, list[int]]:
    """Seconds per zone (index 0 = rest, 1..5) per local date, oldest first."""
    days: dict[str, list[int]] = {}
    for unix, hr in rows:
        date = dt.datetime.fromtimestamp(unix).astimezone().strftime("%Y-%m-%d")
        days.setdefault(date, [0] * 6)[zone_of(hr, max_hr)] += 1
    return dict(sorted(days.items()))


def window_start(days: int, now: dt.datetime | None = None) -> dt.datetime:
    """Local midnight `days - 1` days ago: the window holds whole local days, today included.

    A window cut at the current time of day would show the first day as a partial count."""
    now = now or dt.datetime.now().astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=days - 1)


def load_rows(db: str, since_unix: int) -> list[tuple[int, int]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT unix, hr FROM hist18 WHERE unix >= ? AND hr > 0 ORDER BY unix", (since_unix,)
        ).fetchall()
    finally:
        conn.close()


def load_data_end(db: str) -> int:
    """Unix time of the newest record. Seconds after it are not drained yet, not unworn."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COALESCE(MAX(unix), 0) FROM hist18").fetchone()[0]
    finally:
        conn.close()


def run_zones(db: str, max_hr: int, days: int = 14, as_json: bool = False) -> int:
    if max_hr < 100:
        print(f"max HR {max_hr} bpm is not plausible")
        return 2
    since = int(window_start(days).timestamp())
    table = day_zones(load_rows(db, since), max_hr)
    if as_json:
        data_end = load_data_end(db)
        for date, secs in table.items():
            print(json.dumps({"date": date, "max_hr": max_hr, "worn_s": sum(secs), "zone_s": secs,
                              "data_end": data_end}))
        return 0
    if not table:
        print(f"no heart-rate records in the last {days} days")
        return 0
    rng = ", ".join(f"Z{k} {lo}-{hi if hi else '...'}" for k, (lo, hi) in enumerate(bounds(max_hr), 1))
    print(f"max HR {max_hr} bpm: rest <{bounds(max_hr)[0][0]}, {rng}   (minutes per day; esforço = Z2 to Z5)")
    print(f"{'date':10}  {'worn h':>6}  {'rest':>5}  {'Z1':>4}  {'Z2':>4}  {'Z3':>4}  {'Z4':>4}  {'Z5':>4}  {'esforço':>7}")
    for date, secs in table.items():
        mins = [round(s / 60) for s in secs]
        print(
            f"{date:10}  {sum(secs) / 3600:6.1f}  {mins[0]:5d}  {mins[1]:4d}  {mins[2]:4d}  {mins[3]:4d}  "
            f"{mins[4]:4d}  {mins[5]:4d}  {sum(mins[2:]):7d}"
        )
    return 0
