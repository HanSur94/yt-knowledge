"""Network adapters: metadata, transcript, sponsor segments, thumbnail."""
import json
import os
import subprocess
import time
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
SPONSOR_CATEGORIES = ["sponsor", "selfpromo", "intro", "outro"]
THUMB_URL = "https://i.ytimg.com/vi/{}/maxresdefault.jpg"
THUMB_FALLBACK = "https://i.ytimg.com/vi/{}/hqdefault.jpg"
SPONSOR_URL = ("https://sponsor.ajay.app/api/skipSegments?videoID={}"
               "&categories=%5B%22sponsor%22%2C%22selfpromo%22%2C%22intro%22%2C%22outro%22%5D")


class NoCaption(Exception):
    """Terminal: the video has no caption track and never will."""


class Retryable(Exception):
    """Transient: block, timeout, 429, 5xx."""


class Blocked(Retryable):
    """The whole IP is blocked, not this video. Retrying the row burns an attempt
    for a condition the row cannot cause, so the worker pauses instead."""


class Gone(Exception):
    """Terminal: private, deleted or removed. Never retried, never deleted - the
    row keeps the title and transcript it already had."""


def metadata(video_id: str, timeout: int = 60) -> dict:
    """yt-dlp --dump-json. No download, no subtitle fetch (that path 429s)."""
    try:
        out = subprocess.run(
            ["yt-dlp", "--dump-json", "--skip-download", "--no-warnings",
             f"https://www.youtube.com/watch?v={video_id}"],
            capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise Retryable(f"yt-dlp timeout after {timeout}s") from exc
    if out.returncode != 0:
        msg = ((out.stderr or "").strip().splitlines() or ["yt-dlp failed"])[-1][:200]
        low = msg.lower()
        if "private video" in low or "video unavailable" in low or "removed" in low:
            raise Gone(msg)
        raise Retryable(msg)
    d = json.loads(out.stdout)
    published = d.get("upload_date")               # YYYYMMDD, not ISO
    if published and len(published) == 8:
        published = f"{published[0:4]}-{published[4:6]}-{published[6:8]}"
    return {
        "title": d.get("title"),
        "channel": d.get("channel"),
        "channel_id": d.get("channel_id"),
        "duration_s": d.get("duration"),
        "published_at": published,
        "language": (d.get("language") or None),
        "desc": (d.get("description") or "")[:4000] or None,
        "chapters": [(float(c["start_time"]), c.get("title")) for c in (d.get("chapters") or [])],
    }


def transcript(video_id: str, languages=("en", "de")):
    """Caption track -> [(start, text)]. Raises NoCaption or Retryable."""
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        AgeRestricted,
        IpBlocked,
        NoTranscriptFound,
        RequestBlocked,
        TranscriptsDisabled,
        VideoUnavailable,
    )

    api = YouTubeTranscriptApi()
    try:
        got = api.fetch(video_id, languages=list(languages))
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable, AgeRestricted) as exc:
        raise NoCaption(type(exc).__name__) from exc
    except (IpBlocked, RequestBlocked) as exc:
        raise Blocked(type(exc).__name__) from exc
    except Exception as exc:                        # network, PoToken, rate limit
        raise Retryable(f"{type(exc).__name__}: {exc}"[:200]) from exc
    return [(float(s.start), s.text.replace("\n", " ").strip()) for s in got.snippets] or _no_caption()


def _no_caption():
    raise NoCaption("empty transcript")


def sponsors(video_id: str, timeout: int = 10) -> list:
    """Public SponsorBlock endpoint. 404 means the video has no segments."""
    req = urllib.request.Request(SPONSOR_URL.format(video_id),
                                 headers={"User-Agent": "yt-knowledge/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise Retryable(f"sponsorblock {exc.code}") from exc
    except Exception as exc:
        raise Retryable(f"sponsorblock {type(exc).__name__}") from exc
    return [(float(s["segment"][0]), float(s["segment"][1]), s.get("category"))
            for s in data if s.get("segment")]


def thumbnail(video_id: str, directory: str, timeout: int = 15) -> str | None:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{video_id}.jpg")
    if os.path.exists(path) and os.path.getsize(path) > 1024:
        return path
    # i.ytimg.com intermittently answers 404 under light load; one delayed pass.
    for attempt in range(2):
        for url in (THUMB_URL.format(video_id), THUMB_FALLBACK.format(video_id)):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    blob = r.read()
                if len(blob) > 1024:
                    with open(path, "wb") as fh:
                        fh.write(blob)
                    return path
            except Exception:  # noqa: BLE001, S112 - one CDN hiccup must not lose the video
                continue
        if attempt == 0:
            time.sleep(2)
    return None
