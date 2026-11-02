import json
import os
import sqlite3
import subprocess
import sys

from bodycontrol.store import Store


def test_ingest_idempotent(tmp_path):
    src = tmp_path / "local.sqlite"
    st = Store(str(src))
    sid = st.start_session("WHOOP", "addr")
    st.add_hr(sid, 1000, 60, True)
    st.add_hr(sid, 2000, 61, None)
    st.close()

    conn = sqlite3.connect(src)
    lines = []
    for t in ("session", "hr_sample"):
        cur = conn.execute(f"SELECT rowid AS _rid, * FROM {t}")
        cols = [d[0] for d in cur.description]
        for r in cur:
            lines.append(json.dumps({"origin": "test", "table": t, "row": dict(zip(cols, r))}))
    data = "\n".join(lines) + "\n"
    dst = tmp_path / "vps.sqlite"
    env = dict(os.environ, BODYCONTROL_DB=str(dst))
    for expected in ("inserted 3", "inserted 0"):
        out = subprocess.run([sys.executable, "-m", "bodycontrol.ingest"], input=data, text=True,
                             capture_output=True, env=env, check=True).stdout
        assert expected in out
    rows = sqlite3.connect(dst).execute("SELECT hr FROM hr_sample ORDER BY ts_ms").fetchall()
    assert rows == [(60,), (61,)]
