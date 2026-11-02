import sqlite3

from bodycontrol.store import Store


def test_pending_rows_do_not_hold_the_write_lock(tmp_path):
    db = str(tmp_path / "t.sqlite")
    st = Store(db)
    sid = st.start_session("WHOOP", "addr")
    for i in range(50):
        st.add_frame_blob(sid, 1000 + i, 1, 47, True, b"\x01")
        st.bump_hist_count(sid, 18)
    # Another writer (the archive job) must get the lock at once, with no wait.
    other = sqlite3.connect(db, timeout=0, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    other.execute("ROLLBACK")
    st.commit()
    assert other.execute("SELECT COUNT(*) FROM frame").fetchone() == (50,)
    assert other.execute("SELECT n FROM hist_count WHERE version = 18").fetchone() == (50,)
    st.close()


def test_failed_commit_keeps_rows(tmp_path):
    db = str(tmp_path / "t.sqlite")
    st = Store(db)
    st.add_battery(1, 1000, 90)
    other = sqlite3.connect(db, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    st.conn.execute("PRAGMA busy_timeout = 0")
    try:
        st.commit()
        raise AssertionError("commit should fail while the other writer holds the lock")
    except sqlite3.OperationalError:
        pass
    other.execute("ROLLBACK")
    st.commit()
    assert other.execute("SELECT percent FROM battery").fetchall() == [(90,)]
    st.close()
