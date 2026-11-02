"""Copy new rows from the local SQLite to the VPS over SSH.

Each row is sent as one JSON line to `python3 -m bodycontrol.ingest` on the VPS.
The VPS inserts with INSERT OR IGNORE on (origin, table, local id), so a resend is harmless.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time

# Decoded tables only. Raw frames go to S3 (see archive.py), never to the VPS disk.
# raw_frame / hist_record are the v1 tables; kept so old local files still push.
TABLES = ("session", "hr_sample", "rr_interval", "battery", "hist18", "hist_count", "hist_record")


def _rows(conn: sqlite3.Connection, table: str, after: int):
    cur = conn.execute(f"SELECT rowid AS _rid, * FROM {table} WHERE rowid > ? ORDER BY rowid", (after,))
    cols = [d[0] for d in cur.description]
    for r in cur:
        yield dict(zip(cols, r))


def run_push(db: str, host: str, remote_dir: str, origin: str) -> int:
    from .store import Store

    conn = Store(db).conn  # applies the current schema to an older file
    conn.execute("CREATE TABLE IF NOT EXISTS push_cursor (tbl TEXT PRIMARY KEY, last_rowid INTEGER NOT NULL)")
    cursors = dict(conn.execute("SELECT tbl, last_rowid FROM push_cursor"))
    lines: list[str] = []
    maxes: dict[str, int] = {}
    for t in TABLES:
        for r in _rows(conn, t, cursors.get(t, 0)):
            maxes[t] = r["_rid"]
            lines.append(json.dumps({"origin": origin, "table": t, "row": r}, separators=(",", ":")))
    if not lines:
        print("nothing new to push")
        return 0
    cmd = ["ssh", "-o", "BatchMode=yes", host, f"cd {remote_dir} && python3 -m bodycontrol.ingest"]
    t0 = time.time()
    res = subprocess.run(cmd, input="\n".join(lines) + "\n", text=True, capture_output=True)
    if res.returncode != 0:
        print("push FAILED:", res.stderr.strip()[-500:])
        return 1
    with conn:
        for t, m in maxes.items():
            conn.execute("INSERT OR REPLACE INTO push_cursor(tbl, last_rowid) VALUES (?, ?)", (t, m))
    print(f"pushed {len(lines)} rows in {time.time() - t0:.1f}s. VPS: {res.stdout.strip()}")
    return 0
