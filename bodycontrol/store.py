"""SQLite storage for live samples."""

from __future__ import annotations

import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id          INTEGER PRIMARY KEY,
    started_ms  INTEGER NOT NULL,
    device_name TEXT,
    device_addr TEXT
);
CREATE TABLE IF NOT EXISTS hr_sample (
    session_id INTEGER NOT NULL REFERENCES session(id),
    ts_ms      INTEGER NOT NULL,
    hr         INTEGER NOT NULL,
    contact    INTEGER
);
CREATE TABLE IF NOT EXISTS rr_interval (
    session_id INTEGER NOT NULL REFERENCES session(id),
    ts_ms      INTEGER NOT NULL,
    rr_ms      INTEGER NOT NULL,
    accepted   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS battery (
    session_id INTEGER NOT NULL REFERENCES session(id),
    ts_ms      INTEGER NOT NULL,
    percent    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_frame (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES session(id),
    ts_ms      INTEGER NOT NULL,
    char       TEXT NOT NULL,
    type       INTEGER NOT NULL,
    crc_ok     INTEGER NOT NULL,
    hex        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hist_record (
    record_index INTEGER PRIMARY KEY,
    unix         INTEGER NOT NULL,
    version      INTEGER NOT NULL,
    hr           INTEGER,
    rr_ms        TEXT,
    dyn_acc_g    REAL,
    grav_x       REAL,
    grav_y       REAL,
    grav_z       REAL,
    skin_temp_c  REAL,
    sleep_state  INTEGER,
    hex          TEXT NOT NULL
);
-- v2 storage: raw frames as BLOB (archived to S3, then pruned locally);
-- decoded v18 records without a hex copy.
CREATE TABLE IF NOT EXISTS frame (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    ts_ms      INTEGER NOT NULL,
    char       INTEGER NOT NULL,
    type       INTEGER NOT NULL,
    crc_ok     INTEGER NOT NULL,
    raw        BLOB NOT NULL,
    archived   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS frame_archived ON frame(archived, id);
CREATE TABLE IF NOT EXISTS hist18 (
    record_index       INTEGER PRIMARY KEY,
    unix               INTEGER NOT NULL,
    hr                 INTEGER,
    rr_ms              TEXT,
    hr_quality_flags   INTEGER,
    cardiac_flags      INTEGER,
    cardiac_status     INTEGER,
    dyn_acc_g          REAL,
    grav_x             REAL,
    grav_y             REAL,
    grav_z             REAL,
    step_counter       INTEGER,
    step_cadence       INTEGER,
    motion_wear        INTEGER,
    temp_aux1_c        REAL,
    temp_aux2_c        REAL,
    skin_temp_c        REAL,
    status_word        INTEGER,
    sleep_state        INTEGER,
    sleep_flags        INTEGER,
    spo2_candidate     INTEGER,
    optical_base_a     INTEGER,
    optical_base_b     INTEGER,
    optical_amp_a      INTEGER,
    optical_amp_b      INTEGER,
    quality_f32        REAL
);
CREATE INDEX IF NOT EXISTS hist18_unix ON hist18(unix);
CREATE TABLE IF NOT EXISTS hist_count (
    session_id INTEGER NOT NULL,
    version    INTEGER NOT NULL,
    n          INTEGER NOT NULL,
    PRIMARY KEY (session_id, version)
);
CREATE INDEX IF NOT EXISTS hr_sample_ts ON hr_sample(ts_ms);
CREATE INDEX IF NOT EXISTS hist_record_unix ON hist_record(unix);
CREATE INDEX IF NOT EXISTS rr_interval_ts ON rr_interval(ts_ms);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


class Store:
    def __init__(self, path: str) -> None:
        # timeout: the daemon and the hourly archive job share this file.
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        # Rows wait here until commit(), then go in as one short transaction. Without this, the
        # first INSERT after each commit opens a new write transaction at once. The daemon then
        # holds the write lock almost all the time, and the archive job gets "database is locked".
        self._pending: list[tuple[str, tuple | dict]] = []

    def _add(self, sql: str, params: tuple | dict) -> None:
        self._pending.append((sql, params))

    def start_session(self, device_name: str | None, device_addr: str | None) -> int:
        self.commit()
        cur = self.conn.execute(
            "INSERT INTO session(started_ms, device_name, device_addr) VALUES (?, ?, ?)",
            (now_ms(), device_name, device_addr),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def add_hr(self, session_id: int, ts_ms: int, hr: int, contact: bool | None) -> None:
        self._add(
            "INSERT INTO hr_sample(session_id, ts_ms, hr, contact) VALUES (?, ?, ?, ?)",
            (session_id, ts_ms, hr, None if contact is None else int(contact)),
        )

    def add_rr(self, session_id: int, ts_ms: int, rr_ms: int, accepted: bool) -> None:
        self._add(
            "INSERT INTO rr_interval(session_id, ts_ms, rr_ms, accepted) VALUES (?, ?, ?, ?)",
            (session_id, ts_ms, rr_ms, int(accepted)),
        )

    def add_battery(self, session_id: int, ts_ms: int, percent: int) -> None:
        self._add(
            "INSERT INTO battery(session_id, ts_ms, percent) VALUES (?, ?, ?)",
            (session_id, ts_ms, percent),
        )

    def add_frame(self, session_id: int, ts_ms: int, char: str, type_: int, crc_ok: bool, hex_: str) -> None:
        self._add(
            "INSERT INTO raw_frame(session_id, ts_ms, char, type, crc_ok, hex) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, ts_ms, char, type_, int(crc_ok), hex_),
        )

    def add_frame_blob(self, session_id: int, ts_ms: int, char: int, type_: int, crc_ok: bool, raw: bytes) -> None:
        self._add(
            "INSERT INTO frame(session_id, ts_ms, char, type, crc_ok, raw) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, ts_ms, char, type_, int(crc_ok), raw),
        )

    def add_hist18(self, r: dict) -> None:
        cols = list(r)
        self._add(
            f"INSERT OR IGNORE INTO hist18({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})", r
        )

    def bump_hist_count(self, session_id: int, version: int) -> None:
        self._add(
            "INSERT INTO hist_count(session_id, version, n) VALUES (?, ?, 1)"
            " ON CONFLICT(session_id, version) DO UPDATE SET n = n + 1",
            (session_id, version),
        )

    def add_hist(self, r: dict) -> None:
        self._add(
            "INSERT OR IGNORE INTO hist_record(record_index, unix, version, hr, rr_ms, dyn_acc_g,"
            " grav_x, grav_y, grav_z, skin_temp_c, sleep_state, hex)"
            " VALUES (:record_index, :unix, :version, :hr, :rr_ms, :dyn_acc_g,"
            " :grav_x, :grav_y, :grav_z, :skin_temp_c, :sleep_state, :hex)",
            r,
        )

    def commit(self) -> None:
        pending = self._pending
        # On an error the rows stay pending, and the next commit() tries them again.
        with self.conn:
            i = 0
            while i < len(pending):
                j = i
                while j < len(pending) and pending[j][0] == pending[i][0]:
                    j += 1
                self.conn.executemany(pending[i][0], [p for _, p in pending[i:j]])
                i = j
        self._pending = []

    def close(self) -> None:
        self.commit()
        self.conn.close()
