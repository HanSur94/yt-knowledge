import pytest

from ytk import db


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.sqlite"))
    db.init(c)
    yield c
    c.close()


def v(conn, vid="dQw4w9WgXcQ"):
    db.upsert_video(conn, {"video_id": vid, "title": "T", "channel": "C"})


def test_merge_within_90_minutes_keeps_earlier_and_longer(conn):
    v(conn)
    assert db.add_watch(conn, "dQw4w9WgXcQ", "2025-01-01T10:00:00Z", 120, "browser") == "new"
    assert db.add_watch(conn, "dQw4w9WgXcQ", "2025-01-01T10:30:00Z", 300, "takeout") == "merged"
    rows = conn.execute("SELECT * FROM watches").fetchall()
    assert len(rows) == 1
    assert rows[0]["watched_at"] == "2025-01-01T10:00:00Z"
    assert rows[0]["dwell_s"] == 300
    assert rows[0]["sources"] == "browser,takeout"


def test_outside_window_is_a_rewatch(conn):
    v(conn)
    db.add_watch(conn, "dQw4w9WgXcQ", "2025-01-01T10:00:00Z", 120, "browser")
    assert db.add_watch(conn, "dQw4w9WgXcQ", "2025-01-01T14:00:00Z", 120, "browser") == "new"
    assert conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 2


def test_merge_keeps_null_dwell_when_other_side_has_one(conn):
    v(conn)
    db.add_watch(conn, "dQw4w9WgXcQ", "2025-01-01T10:00:00Z", None, "takeout")
    db.add_watch(conn, "dQw4w9WgXcQ", "2025-01-01T10:20:00Z", 240, "browser")
    assert conn.execute("SELECT dwell_s FROM watches").fetchone()[0] == 240


def test_reimport_never_resurrects_state(conn):
    v(conn)
    conn.execute("UPDATE videos SET state='archived', name=1" if False else
                 "UPDATE videos SET state='archived'")
    db.upsert_video(conn, {"video_id": "dQw4w9WgXcQ", "title": "New", "channel": "C2"})
    row = conn.execute("SELECT state, title, channel FROM videos").fetchone()
    assert row["state"] == "archived"
    assert row["title"] == "New" and row["channel"] == "C2"


def test_segment_delete_keeps_fts_in_sync(conn):
    v(conn)
    conn.execute("INSERT INTO segments(video_id,start_s,text) VALUES ('dQw4w9WgXcQ',1.0,'postgres index bloat')")
    conn.execute("INSERT INTO segments(video_id,start_s,text) VALUES ('dQw4w9WgXcQ',2.0,'other line')")
    assert conn.execute(
        "SELECT COUNT(*) FROM segments_fts WHERE segments_fts MATCH 'bloat'").fetchone()[0] == 1
    # the documented refetch path
    conn.execute("DELETE FROM segments WHERE video_id='dQw4w9WgXcQ'")
    conn.execute("INSERT INTO segments(video_id,start_s,text) VALUES ('dQw4w9WgXcQ',1.0,'postgres index bloat')")
    hits = conn.execute(
        "SELECT COUNT(*) FROM segments_fts WHERE segments_fts MATCH 'bloat'").fetchone()[0]
    assert hits == 1, "duplicate index entries: the REPLACE trap"


def test_user_version_mismatch_refuses_to_start(tmp_path):
    c = db.connect(str(tmp_path / "bad.sqlite"))
    db.init(c)
    c.execute("PRAGMA user_version=99")
    with pytest.raises(SystemExit):
        db.init(c)
    c.close()


def test_claim_is_exclusive_and_resumable(conn):
    v(conn, "aaaaaaaaaaa")
    v(conn, "bbbbbbbbbbb")
    first = db.claim_next(conn)
    assert first is not None and first["state"] == "running"
    second = db.claim_next(conn)
    assert second["id"] != first["id"], "same row handed out twice"
    assert db.resume_stuck(conn) == 2
    assert conn.execute("SELECT COUNT(*) FROM videos WHERE state='running'").fetchone()[0] == 0


def test_search_finds_a_phrase_that_exists_only_in_speech(conn):
    v(conn)
    db.save_fetch(conn, "dQw4w9WgXcQ", {"title": "T", "channel": "C"}, 
                  [(1.0, "this is a confusing acquisition to me"),
                   (2.0, "and then something unrelated")], [], [])
    hits = db.search(conn, "confusing acquisition")
    assert len(hits) == 1
    assert hits[0]["kind"] == "transcript"
    assert hits[0]["ts"] == 1.0
    assert db.search(conn, "no_such_phrase_here") == []


def test_summary_lands_in_the_index_once(conn):
    v(conn)
    s = {"headline": "A claim", "bullets": [(5, "point one"), (9, "point two")],
         "tags": ["x"], "lang": "en", "short_en": "A claim", "model": "test",
         "title": "T", "channel": "C"}
    db.save_summary(conn, "dQw4w9WgXcQ", s)
    db.save_summary(conn, "dQw4w9WgXcQ", s)          # regenerate
    assert conn.execute("SELECT COUNT(*) FROM search_docs").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM search_docs_fts WHERE search_docs_fts MATCH 'claim'").fetchone()[0] == 1


def test_save_summary_without_a_model_key(conn):
    """The worker builds its dict without 'model'; the NAS crashed 35 rows on it."""
    v(conn)
    db.save_summary(conn, "dQw4w9WgXcQ", {
        "headline": "H", "bullets": [(1, "b")], "tags": [], "lang": "en",
        "short_en": "H", "title": "T", "channel": "C"})
    row = conn.execute("SELECT model, headline FROM summaries").fetchone()
    assert row["headline"] == "H" and row["model"]
