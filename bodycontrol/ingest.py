"""VPS side: read JSON lines from stdin, store them in data/bodycontrol.sqlite.

Stdlib only. Every table gets (origin, local_id) as its unique key.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

DB = os.environ.get("BODYCONTROL_DB", os.path.join(os.path.dirname(__file__), "..", "data", "bodycontrol.sqlite"))


def main() -> int:
    os.makedirs(os.path.dirname(os.path.abspath(DB)), exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    known: dict[str, list[str]] = {}
    n = new = 0
    with conn:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            table, row, origin = msg["table"], msg["row"], msg["origin"]
            if not table.isidentifier():
                raise ValueError(f"bad table {table!r}")
            local_id = row.pop("_rid")
            row.pop("id", None)
            cols = sorted(row)
            if table not in known:
                cdefs = ", ".join(f'"{c}"' for c in cols)
                conn.execute(
                    f'CREATE TABLE IF NOT EXISTS "{table}" (origin TEXT NOT NULL, local_id INTEGER NOT NULL, '
                    f"{cdefs}, PRIMARY KEY (origin, local_id))"
                )
                have = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
                for c in cols:
                    if c not in have:
                        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{c}"')
                known[table] = cols
            ph = ", ".join("?" for _ in cols)
            names = ", ".join(f'"{c}"' for c in cols)
            cur = conn.execute(
                f'INSERT OR IGNORE INTO "{table}" (origin, local_id, {names}) VALUES (?, ?, {ph})',
                [origin, local_id] + [row[c] for c in cols],
            )
            n += 1
            new += cur.rowcount
    print(f"received {n}, inserted {new}, db {os.path.abspath(DB)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
