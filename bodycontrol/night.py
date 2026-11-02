"""Nightly resting heart rate and HRV from the 1 Hz history (table hist18).

Method. It is the one that noop, OpenStrap and whoop-rs ship, and the one that
came closest to WHOOP's own numbers in their comparisons:

- A night is the set of records the strap marks asleep (sleep_state 2). Two
  asleep stretches less than `merge_gap_s` apart belong to the same night, so a
  short wake does not split it. A night with less than `min_sleep_s` of sleep
  gives no numbers: short sleeps and naps give high HRV values without meaning.
- RHR = median of the 1 Hz heart rate while asleep. whoop-rs found the median
  within 1 bpm of WHOOP's RHR; the lowest 5-min window read 11 bpm low.
- HRV = RMSSD of the R-R intervals, in ms. Beats pass the Malik rule: 300-2000 ms
  and within 20 % of the median of the 4 neighbours. A rejected beat breaks the
  run, so no difference is taken across it (gap-aware). Each 5-min window needs
  at least `min_diffs` differences. The night's value is the mean of the windows.
  WHOOP uses overlapping windows and weights sleep stages; we have no stages yet.
- A night is complete only when the records go on for at least `TAIL_S` after its
  last asleep record. If they stop earlier, the history may still be on the strap:
  the wake time and the duration are unknown, and the output says so.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import statistics
from collections.abc import Iterable

WINDOW_S = 300
MIN_DIFFS = 20
MERGE_GAP_S = 3600
MIN_SLEEP_S = 3 * 3600
RR_MIN, RR_MAX = 300, 2000
MALIK = 0.20
TAIL_S = 300


def clean_runs(rr: list[int]) -> list[list[int]]:
    """Split a beat sequence into runs of accepted beats. A rejected beat ends the run."""
    runs: list[list[int]] = [[]]
    for i, x in enumerate(rr):
        nb = [rr[j] for j in (i - 2, i - 1, i + 1, i + 2) if 0 <= j < len(rr)]
        ok = RR_MIN <= x <= RR_MAX
        if ok and nb:
            med = statistics.median(nb)
            ok = abs(x - med) <= MALIK * med
        if ok:
            runs[-1].append(x)
        elif runs[-1]:
            runs.append([])
    return [r for r in runs if len(r) >= 2]


def rmssd(runs: list[list[int]], min_diffs: int = MIN_DIFFS) -> float | None:
    """Root mean square of successive differences, only inside runs."""
    sq = [(b - a) ** 2 for r in runs for a, b in zip(r, r[1:])]
    if len(sq) < min_diffs:
        return None
    return math.sqrt(sum(sq) / len(sq))


def find_nights(
    rows: Iterable[tuple[int, int | None, str | None, int | None]],
    merge_gap_s: int = MERGE_GAP_S,
    min_sleep_s: int = MIN_SLEEP_S,
) -> list[list[tuple[int, int | None, str | None]]]:
    """Group asleep records (unix, hr, rr_json, sleep_state) into nights, oldest first."""
    nights: list[list[tuple[int, int | None, str | None]]] = []
    for unix, hr, rr, state in sorted(rows):
        if state != 2:
            continue
        if nights and unix - nights[-1][-1][0] <= merge_gap_s:
            nights[-1].append((unix, hr, rr))
        else:
            nights.append([(unix, hr, rr)])
    return [n for n in nights if len(n) >= min_sleep_s]


def night_summary(records: list[tuple[int, int | None, str | None]]) -> dict:
    start = records[0][0]
    hr = [h for _, h, _ in records if h]
    windows: dict[int, list[int]] = {}
    beats = 0
    for unix, _, rr in records:
        for x in json.loads(rr or "[]"):
            windows.setdefault((unix - start) // WINDOW_S, []).append(x)
            beats += 1
    values = []
    kept = 0
    for _, seq in sorted(windows.items()):
        runs = clean_runs(seq)
        kept += sum(len(r) for r in runs)
        v = rmssd(runs)
        if v is not None:
            values.append(v)
    return {
        "start": start,
        "end": records[-1][0],
        "asleep_s": len(records),
        "rhr": statistics.median(hr) if hr else None,
        "hrv": statistics.mean(values) if values else None,
        "hrv_median": statistics.median(values) if values else None,
        "windows": len(values),
        "beats": beats,
        "rejected": beats - kept,
    }


def is_complete(night_end: int, data_end: int, tail_s: int = TAIL_S) -> bool:
    """True when the data continues for `tail_s` or more after the night's last asleep record."""
    return data_end - night_end >= tail_s


def load_rows(db: str, since_unix: int) -> list[tuple[int, int | None, str | None, int | None]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT unix, hr, rr_ms, sleep_state FROM hist18 WHERE unix >= ? ORDER BY unix", (since_unix,)
        ).fetchall()
    finally:
        conn.close()


def run_night(db: str, days: int = 30, as_json: bool = False) -> int:
    since = int(dt.datetime.now(dt.UTC).timestamp()) - days * 86400
    rows = load_rows(db, since)
    data_end = max((r[0] for r in rows), default=0)
    nights = [night_summary(n) for n in find_nights(rows)]
    for n in nights:
        n["complete"] = is_complete(n["end"], data_end)
        n["data_end"] = data_end
    local = lambda u: dt.datetime.fromtimestamp(u).astimezone()  # noqa: E731
    if as_json:
        for n in nights:
            n["date"] = local(n["end"]).strftime("%Y-%m-%d")
            print(json.dumps(n))
        return 0
    if not nights:
        print(f"no night with {MIN_SLEEP_S // 3600} h or more of sleep in the last {days} days")
        return 0
    print(f"{'date':10}  {'asleep':>13}  {'hours':>5}  {'RHR':>3}  {'HRV':>5}  {'windows':>7}  {'beats':>6}  {'rej%':>4}")
    for n in nights:
        hrv = f"{n['hrv']:5.1f}" if n["hrv"] is not None else "    -"
        rhr = f"{n['rhr']:3.0f}" if n["rhr"] is not None else "  -"
        end = f"{local(n['end']):%H:%M}" if n["complete"] else "?    "
        hours = f"{n['asleep_s'] / 3600:5.1f}" if n["complete"] else "    -"
        note = "" if n["complete"] else f"  incomplete: data ends {local(n['data_end']):%H:%M}"
        print(
            f"{local(n['end']):%Y-%m-%d}  {local(n['start']):%H:%M}-{end} {'':6}"
            f"{hours}  {rhr}  {hrv}  {n['windows']:7d}  {n['beats']:6d}  "
            f"{100 * n['rejected'] / n['beats'] if n['beats'] else 0:4.1f}{note}"
        )
    return 0
