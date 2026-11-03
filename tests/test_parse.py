import io
import json
import zipfile

from ytk.ingest import (
    extract_video_id,
    ingest_filter,
    parse_iso,
    parse_jsonl,
    parse_takeout,
)


def test_extract_all_url_forms():
    for url in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://youtu.be/dQw4w9WgXcQ",
                "https://www.youtube.com/shorts/dQw4w9WgXcQ",
                "https://www.youtube.com/embed/dQw4w9WgXcQ",
                "https://www.youtube.com/live/dQw4w9WgXcQ"):
        assert extract_video_id(url) == "dQw4w9WgXcQ", url


def test_rejects_malformed_id():
    for url in ("https://www.youtube.com/watch?v=abc", "https://example.com/x", "",
                "https://www.youtube.com/watch?v=abcdefghij"):   # 10 chars
        assert extract_video_id(url) is None, url


def test_filter_boundaries():
    assert ingest_filter({"dwell_s": 60})[0] is True
    assert ingest_filter({"dwell_s": 59}) == (False, "under_60s")
    assert ingest_filter({"dwell_s": None})[0] is True        # Takeout/Safari: no dwell
    assert ingest_filter({"dwell_s": 3600, "is_short": True}) == (False, "shorts")


def test_jsonl_roundtrip_and_bad_rows():
    good = {"video_id": "dQw4w9WgXcQ", "title": "T", "channel": "C",
            "watched_at": "2026-01-02T03:04:05Z", "dwell_s": 120, "source": "browser"}
    body = ("\n".join([json.dumps(good), "not json at all", "{}",
                       json.dumps({**good, "watched_at": "nope"})])).encode()
    rows = parse_jsonl(body)
    assert len(rows) == 1
    assert rows[0]["watched_at"] == "2026-01-02T03:04:05Z"


def test_takeout_json_only_counts_watched_rows():
    items = [
        {"header": "YouTube", "title": "Watched Foo",
         "titleUrl": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
         "subtitles": [{"name": "Chan"}], "time": "2025-03-01T10:00:00.123Z"},
        {"header": "YouTube", "title": "Watched an ad", "details": [{"name": "From Google Ads"}],
         "titleUrl": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "time": "2025-03-01T10:00:00.123Z"},
        {"header": "YouTube", "title": "Searched for x", "time": "2025-03-01T10:00:00.123Z"},
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Takeout/YouTube and YouTube Music/history/watch-history.json",
                    json.dumps(items))
    rows = parse_takeout(buf.getvalue())
    assert len(rows) == 1
    assert rows[0]["channel"] == "Chan"
    assert rows[0]["title"] == "Foo"


def test_takeout_html_fallback_is_best_effort():
    """Synthetic fixture: proves the parser against the assumed shape, not
    against Google's current output."""
    doc = ('<div><a href="https://www.youtube.com/watch?v=dQw4w9WgXcQ">Foo</a><br>'
           '<span>1. Mai 2025, 12:34:56 CEST</span></div>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Takeout/YouTube and YouTube Music/history/watch-history.html", doc)
    rows = parse_takeout(buf.getvalue())
    assert len(rows) == 1
    assert rows[0]["watched_at"].startswith("2025-05-01T12:34:56")


def test_missing_zip_members_returns_empty():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Takeout/other.txt", "x")
    assert parse_takeout(buf.getvalue()) == []


def test_iso_parsing_accepts_z_and_offset():
    assert parse_iso("2026-01-01T00:00:00Z").tzinfo is not None
    assert parse_iso("2026-01-01T00:00:00+00:00") is not None
    assert parse_iso("garbage") is None
