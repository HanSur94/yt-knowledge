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


def test_search_matches_words_in_any_order(conn):
    """The archive is asked questions, not quoted phrases."""
    v(conn)
    db.save_fetch(conn, "dQw4w9WgXcQ", {"title": "T", "channel": "C"},
                  [(1.0, "a confusing acquisition for the team")], [], [])
    assert len(db.search(conn, "acquisition confusing")) == 1


def test_fail_parks_a_hopeless_video(tmp_path):
    from datetime import UTC, datetime

    from ytk import db

    conn = db.connect(str(tmp_path / "t.sqlite"))
    db.init(conn)
    db.upsert_video(conn, {"video_id": "aaaaaaaaaaa", "title": "T", "channel": "C"})
    conn.execute("UPDATE videos SET attempts=19")
    now = int(datetime.now(UTC).timestamp())
    db.fail(conn, "aaaaaaaaaaa", "fetch", "blocked")
    attempts, due = conn.execute("SELECT attempts, next_attempt_at FROM videos").fetchone()
    assert attempts == 20
    assert due - now >= 7 * 86400 - 5          # parked for a week, not one hour
    conn.close()


def _two_videos(conn):
    v(conn, "aaaaaaaaaaa")
    v(conn, "bbbbbbbbbbb")
    db.upsert_video(conn, {"video_id": "bbbbbbbbbbb", "title": "Other", "channel": "D"})
    db.save_fetch(conn, "aaaaaaaaaaa", {"title": "T", "channel": "C"},
                  [(10.0, "the acquisition closed"), (20.0, "nothing here"),
                   (30.0, "a second acquisition <b>later</b>"), (40.0, "acquisition three")],
                  [], [])
    db.save_fetch(conn, "bbbbbbbbbbb", {"title": "Other", "channel": "D"},
                  [(5.0, "one acquisition only")], [], [])


def test_grouped_search_keeps_every_moment_of_a_video(conn):
    """The old search kept one line per video and dropped where else it was said."""
    _two_videos(conn)
    r = db.search_grouped(conn, "acquisition", moments=2)
    assert [x["id"] for x in r["videos"]] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    top = r["videos"][0]
    ts = [m[0] for m in top["moments"]]
    assert top["hits"] == 3 and len(ts) == 2 and ts == sorted(ts)   # best two, in time order
    assert r["total_moments"] == 4
    assert dict(r["channels"]) == {"C": 1, "D": 1}


def test_grouped_search_escapes_text_and_marks_only_the_match(conn):
    _two_videos(conn)
    r = db.search_grouped(conn, "later")
    html = str(r["videos"][0]["moments"][0][1])
    assert "<b>" not in html and "&lt;b&gt;" in html
    assert "<mark>later</mark>" in html


def test_grouped_search_channel_filter_keeps_channel_counts(conn):
    _two_videos(conn)
    r = db.search_grouped(conn, "acquisition", channel="D")
    assert [x["id"] for x in r["videos"]] == ["bbbbbbbbbbb"]
    assert len(r["channels"]) == 2


def test_grouped_search_matches_titles_by_whole_word(conn):
    db.upsert_video(conn, {"video_id": "ccccccccccc", "title": "How to maintain a bike",
                           "channel": "X"})
    db.upsert_video(conn, {"video_id": "ddddddddddd", "title": "AI in 2026", "channel": "X"})
    ids = [x["id"] for x in db.search_grouped(conn, "ai")["videos"]]
    assert ids == ["ddddddddddd"]


def test_grouped_search_suggests_words_that_do_occur(conn):
    _two_videos(conn)
    r = db.search_grouped(conn, "acquisition zebra")
    assert r["videos"] == [] and r["suggest"] == [("acquisition", 4)]
