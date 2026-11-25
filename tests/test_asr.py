"""The speech-recognition fallback: when it runs, when it must not, and that
the row it produces looks like any other transcript."""
import pytest

from ytk import asr, db, fetch, worker

SEGS = [(0.0, "hallo"), (2.5, "welt")]


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.sqlite"))
    db.init(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setitem(worker.STATE, "captions_blocked_until", 0.0)
    monkeypatch.delenv("YTK_LLM_BASE", raising=False)


def _meta(lang=None):
    return {"title": "T", "channel": "C", "duration_s": 600, "language": lang,
            "chapters": []}


def _asr(monkeypatch, on=True, calls=None):
    monkeypatch.setattr(asr, "enabled", lambda: on)

    def fake(vid, language):
        if calls is not None:
            calls.append((vid, language))
        return list(SEGS), "de"
    monkeypatch.setattr(asr, "transcript", fake)


def _blocked(*_a, **_k):
    raise fetch.Blocked("IpBlocked")


def test_captions_still_come_first(monkeypatch):
    calls = []
    _asr(monkeypatch, calls=calls)
    monkeypatch.setattr(fetch, "transcript", lambda vid, langs: [(1.0, "cap")])
    assert worker._transcript("v", _meta()) == ([(1.0, "cap")], "captions")
    assert calls == []


def test_caption_block_falls_back_and_skips_captions_afterwards(monkeypatch):
    calls, tries = [], []
    _asr(monkeypatch, calls=calls)

    def blocked(vid, langs):
        tries.append(vid)
        raise fetch.Blocked("IpBlocked")
    monkeypatch.setattr(fetch, "transcript", blocked)
    meta = _meta()
    assert worker._transcript("a", meta) == (SEGS, "whisper")
    assert meta["language"] == "de"            # detected language is kept
    # the block is per IP: the next row must not pay a doomed caption request
    assert worker._transcript("b", _meta()) == (SEGS, "whisper")
    assert tries == ["a"]
    assert [c[0] for c in calls] == ["a", "b"]


def test_metadata_language_is_passed_to_whisper(monkeypatch):
    calls = []
    _asr(monkeypatch, calls=calls)
    monkeypatch.setattr(fetch, "transcript", _blocked)
    worker._transcript("v", _meta("de-DE"))
    assert calls == [("v", "de-DE")]


def test_no_captions_falls_back(monkeypatch):
    _asr(monkeypatch)

    def none(vid, langs):
        raise fetch.NoCaption("TranscriptsDisabled")
    monkeypatch.setattr(fetch, "transcript", none)
    assert worker._transcript("v", _meta())[1] == "whisper"


def test_without_asr_behaviour_is_unchanged(monkeypatch):
    _asr(monkeypatch, on=False)
    monkeypatch.setattr(fetch, "transcript", _blocked)
    with pytest.raises(fetch.Blocked):
        worker._transcript("v", _meta())


def test_tick_stores_a_whisper_transcript(conn, tmp_path, monkeypatch):
    db.upsert_video(conn, {"video_id": "dQw4w9WgXcQ", "title": "T", "channel": "C"})
    _asr(monkeypatch)
    monkeypatch.setattr(fetch, "metadata", lambda vid: _meta())
    monkeypatch.setattr(fetch, "transcript", _blocked)
    monkeypatch.setattr(fetch, "sponsors", lambda vid: [])
    monkeypatch.setattr(fetch, "thumbnail", lambda vid, d: None)
    cfg = type("C", (), {"db": str(tmp_path / "t.sqlite"), "thumbs": str(tmp_path)})()
    assert worker._tick(conn, cfg) == "dQw4w9WgXcQ"
    row = conn.execute("SELECT state, transcript_source, language FROM videos").fetchone()
    assert tuple(row) == ("fetched", "whisper", "de")
    assert conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 2
    # and it is searchable like a caption transcript
    assert conn.execute("SELECT COUNT(*) FROM segments_fts WHERE segments_fts MATCH 'welt'"
                        ).fetchone()[0] == 1


def test_audio_block_still_pauses_the_worker(conn, tmp_path, monkeypatch):
    db.upsert_video(conn, {"video_id": "dQw4w9WgXcQ", "title": "T", "channel": "C"})
    monkeypatch.setattr(asr, "enabled", lambda: True)
    monkeypatch.setattr(asr, "transcript", lambda vid, lang: _blocked())
    monkeypatch.setattr(fetch, "metadata", lambda vid: _meta())
    monkeypatch.setattr(fetch, "transcript", _blocked)
    cfg = type("C", (), {"db": str(tmp_path / "t.sqlite"), "thumbs": str(tmp_path)})()
    assert worker._tick(conn, cfg).startswith("blocked:")
    assert tuple(conn.execute("SELECT state, attempts FROM videos").fetchone()) == ("pending", 0)


@pytest.mark.parametrize("line, kind", [
    ("ERROR: unable to download video data: HTTP Error 429: Too Many Requests", fetch.Blocked),
    ("ERROR: [youtube] x: Sign in to confirm you're not a bot.", fetch.Blocked),
    ("ERROR: [youtube] x: Sign in to confirm your age.", fetch.NoCaption),
    ("ERROR: [youtube] x: Video unavailable", fetch.Gone),
    ("ERROR: [youtube] x: Private video", fetch.Gone),
    ("ERROR: something new", fetch.Retryable),
])
def test_yt_dlp_errors_are_classified(line, kind):
    err = asr._classify(line)
    assert type(err) is kind


def test_v5_database_gains_the_column(tmp_path):
    c = db.connect(str(tmp_path / "old.sqlite"))
    c.executescript(db.SCHEMA.replace(",\n  transcript_source TEXT", ""))
    c.execute("PRAGMA user_version=5")
    c.execute("INSERT INTO videos(id, first_seen, last_seen) VALUES ('a','x','x'),('b','x','x')")
    c.execute("INSERT INTO segments(video_id, start_s, text) VALUES ('a', 0, 'hi')")
    db.init(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 6
    assert dict(c.execute("SELECT id, transcript_source FROM videos").fetchall()) == {
        "a": "captions", "b": None}
