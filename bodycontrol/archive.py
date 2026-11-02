"""Archive raw frames to S3 as compressed files, then prune them locally.

File format (.bcf.xz), LZMA-compressed:
  magic  b"BCF1"
  repeat: ts_ms u64 LE | char u8 | type u8 | crc_ok u8 | len u16 LE | raw bytes
Key: raw/YYYY/MM/DD/<origin>-<first ts_ms>-<first id>-<last id>.bcf.xz  (UTC date of the first frame)
Frame ids are not unique over time: SQLite gives id 1 again when the frame table is empty after
a prune. The first timestamp keeps each key unique. The same rows always give the same key, so a
retry after a failed local delete overwrites the file with the same content.
"""

from __future__ import annotations

import datetime as dt
import lzma
import os
import struct
import subprocess
import tempfile
from collections.abc import Iterator

from .store import Store

MAGIC = b"BCF1"
HEAD = struct.Struct("<QBBBH")
DEFAULT_BUCKET = os.environ.get("BODYCONTROL_BUCKET")  # your own S3 bucket
DEFAULT_PROFILE = os.environ.get("BODYCONTROL_AWS_PROFILE", "default")


def pack(rows) -> bytes:
    out = bytearray(MAGIC)
    for ts_ms, char, type_, crc_ok, raw in rows:
        out += HEAD.pack(ts_ms, char & 0xFF, type_ & 0xFF, int(crc_ok), len(raw)) + bytes(raw)
    return lzma.compress(bytes(out), preset=6)


def unpack(data: bytes) -> Iterator[tuple[int, int, int, int, bytes]]:
    buf = lzma.decompress(data)
    if buf[:4] != MAGIC:
        raise ValueError("not a BCF1 file")
    i = 4
    while i < len(buf):
        ts_ms, char, type_, crc_ok, n = HEAD.unpack_from(buf, i)
        i += HEAD.size
        yield ts_ms, char, type_, crc_ok, buf[i : i + n]
        i += n


def archive_key(origin: str, first_ts_ms: int, first_id: int, last_id: int) -> str:
    day = dt.datetime.fromtimestamp(first_ts_ms / 1000, dt.UTC)
    return f"raw/{day:%Y/%m/%d}/{origin}-{first_ts_ms}-{first_id:010d}-{last_id:010d}.bcf.xz"


def run_archive(db: str, origin: str, bucket: str, profile: str, keep_days: float = 0.0, batch: int = 20000) -> int:
    st = Store(db)
    conn = st.conn
    total = 0
    # Stop at the newest frame that exists now. The daemon writes new frames during each upload;
    # without this bound the loop chases them in tiny batches and never ends.
    (end_id,) = conn.execute("SELECT COALESCE(MAX(id), 0) FROM frame").fetchone()
    while True:
        rows = conn.execute(
            "SELECT id, ts_ms, char, type, crc_ok, raw FROM frame WHERE archived = 0 AND id <= ? ORDER BY id LIMIT ?",
            (end_id, batch),
        ).fetchall()
        if not rows:
            break
        first, last = rows[0][0], rows[-1][0]
        key = archive_key(origin, rows[0][1], first, last)
        blob = pack((r[1], r[2], r[3], r[4], r[5]) for r in rows)
        with tempfile.NamedTemporaryFile(suffix=".bcf.xz", delete=False) as tf:
            tf.write(blob)
            tmp = tf.name
        try:
            res = subprocess.run(
                ["aws", "s3", "cp", "--profile", profile, "--only-show-errors", tmp, f"s3://{bucket}/{key}"],
                capture_output=True,
                text=True,
            )
        finally:
            os.unlink(tmp)
        if res.returncode != 0:
            print("archive upload FAILED:", res.stderr.strip()[-400:])
            st.close()
            return 1
        with conn:
            if keep_days <= 0:
                # S3 confirmed the upload: the cloud copy is the only copy kept.
                conn.execute("DELETE FROM frame WHERE archived = 0 AND id BETWEEN ? AND ?", (first, last))
            else:
                conn.execute("UPDATE frame SET archived = 1 WHERE archived = 0 AND id BETWEEN ? AND ?", (first, last))
        raw_bytes = sum(len(r[5]) for r in rows)
        print(f"archived {len(rows)} frames ({raw_bytes / 1024:.0f} KB raw -> {len(blob) / 1024:.0f} KB) to s3://{bucket}/{key}")
        total += len(rows)
    cutoff = int((dt.datetime.now(dt.UTC).timestamp() - keep_days * 86400) * 1000)
    with conn:
        n = conn.execute("DELETE FROM frame WHERE archived = 1 AND ts_ms < ?", (cutoff,)).rowcount
    if n:
        print(f"pruned {n} archived frames older than {keep_days:g} days")
    # No VACUUM: the daemon writes continuously; freed pages are reused for new frames.
    if total == 0:
        print("nothing new to archive")
    st.close()
    return 0
