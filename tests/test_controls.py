"""The four controls that fail silently: a pause that does not hold, a retry
that does not move the row, a state nothing claims, and an uncapped backoff."""
import time

import pytest

from ytk import db, worker


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.sqlite"))
    db.init(c)
    yield c
    c.close()


def v(conn, vid="dQw4w9WgXcQ"):
    db.upsert_video(conn, {"video_id": vid, "title": "T", "channel": "C"})


def test_pause_marker_stops_the_tick(conn, tmp_path):
    v(conn)
    cfg = type("C", (), {"db": str(tmp_path / "t.sqlite")})()
    open(tmp_path / "paused", "a").close()
    assert worker._tick(conn, cfg) is None
    assert conn.execute("SELECT state FROM videos").fetchone()[0] == "pending"
    (tmp_path / "paused").unlink()
    # the marker was the only obstacle: the row is claimable the moment it goes
    assert db.claim_next(conn)["id"] == "dQw4w9WgXcQ"


def test_retry_resets_every_column_and_jumps_the_queue(conn):
    # a row that failed, plus a backlog that would otherwise sort ahead of it
    v(conn, "aaaaaaaaaaa")
    db.add_watch(conn, "aaaaaaaaaaa", "2025-01-01T10:00:00Z", 600, "browser")
    db.fail(conn, "aaaaaaaaaaa", "summarize", "boom")
    v(conn, "bbbbbbbbbbb")
    now = int(time.time()) + 10_000           # backlog is not due, the retry is
    conn.execute("UPDATE videos SET next_attempt_at=? WHERE id='bbbbbbbbbbb'", (now,))

    assert db.retry(conn, "aaaaaaaaaaa") == 1
    row = conn.execute("SELECT * FROM videos WHERE id='aaaaaaaaaaa'").fetchone()
    assert (row["state"], row["attempts"], row["next_attempt_at"],
            row["fail_stage"], row["fail_reason"]) == ("pending", 0, -1, None, None)
    # -1 sorts ahead of every NULL, and the backlog is not due yet
    assert db.claim_next(conn)["id"] == "aaaaaaaaaaa"


def test_fetched_rows_are_claimable(conn):
    """Rev 1 shipped a Resummarize button that parked rows in a state nothing
    claimed: the row had segments, no summary, and waited forever."""
    v(conn)
    conn.execute("UPDATE videos SET state='fetched' WHERE id='dQw4w9WgXcQ'")
    assert db.claim_next(conn)["id"] == "dQw4w9WgXcQ"


def test_backoff_is_capped_at_one_hour(conn):
    v(conn)
    conn.execute("UPDATE videos SET attempts=9 WHERE id='dQw4w9WgXcQ'")
    before = int(time.time())
    db.fail(conn, "dQw4w9WgXcQ", "fetch", "boom")
    due = conn.execute("SELECT next_attempt_at FROM videos").fetchone()[0]
    assert 3_500 < due - before <= 3_601


def test_retry_failed_moves_only_failed_rows(conn):
    v(conn, "aaaaaaaaaaa")
    v(conn, "bbbbbbbbbbb")
    db.fail(conn, "aaaaaaaaaaa", "fetch", "boom")
    conn.execute("UPDATE videos SET state='ready' WHERE id='bbbbbbbbbbb'")
    assert db.retry_failed(conn) == 1
    states = dict(conn.execute("SELECT id, state FROM videos").fetchall())
    assert states == {"aaaaaaaaaaa": "pending", "bbbbbbbbbbb": "ready"}


def test_blocked_count_sees_the_block_path(conn):
    v(conn)
    assert db.blocked_count(conn) == 0
    conn.execute("UPDATE videos SET state='pending', next_attempt_at=?",
                 (int(time.time()) + 600,))
    assert db.blocked_count(conn) == 1


def test_safe_forwards_arguments():
    """The thumbnail call needs (id, dir); dropping the second argument was
    swallowed by the catch-all and stored the directory as the image path."""
    assert worker._safe(lambda a, b: b, "vid", "/data/thumbs") == "/data/thumbs"
    assert worker._safe(lambda a, b: 1 / 0, "vid", "/data/thumbs", default=None) is None


def test_settings_defaults_and_roundtrip(conn):
    assert db.get_setting(conn, "min_duration_s") == "120"
    assert db.get_setting(conn, "max_duration_s") == "0"
    db.set_setting(conn, "max_duration_s", 1800)
    assert db.get_setting(conn, "max_duration_s") == "1800"
    db.set_setting(conn, "max_duration_s", 3600)          # upsert, not duplicate
    assert db.get_setting(conn, "max_duration_s") == "3600"
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 1


def test_schema_is_v4_with_a_settings_table(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4


def test_duration_window():
    from ytk.worker import _duration_skip
    assert _duration_skip(60, 120, 0) == "shorter than 2 min"
    assert _duration_skip(3600, 120, 1800) == "longer than 30 min"
    assert _duration_skip(600, 120, 1800) is None
    assert _duration_skip(99999, 120, 0) is None          # 0 disables the ceiling
    assert _duration_skip(None, 120, 1800) is None        # unknown yet
