"""Input adapters. Everything normalises to one record shape:
{video_id, title, channel, watched_at, dwell_s, source}."""
import html as htmllib
import io
import json
import re
import zipfile
from datetime import UTC, datetime

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_URL_ID = re.compile(
    r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})")
_SHORTS = re.compile(r"youtube\.com/shorts/")


def extract_video_id(url: str) -> str | None:
    if not url:
        return None
    m = _URL_ID.search(url)
    if not m:
        return None
    vid = m.group(1)
    return vid if VIDEO_ID.match(vid) else None


def iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        # A trailing Z parses as naive; astimezone() would then apply the LOCAL
        # offset and shift every timestamp. Stamp UTC before converting.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    return None


def ingest_filter(rec) -> tuple[bool, str]:
    """Stage-1 rule only: dwell floor, and Shorts by URL. The duration rule
    needs metadata and lives in the worker."""
    if rec.get("is_short"):
        return False, "shorts"
    dwell = rec.get("dwell_s")
    if dwell is None:
        return True, "no_dwell"
    if dwell < 60:
        return False, "under_60s"
    return True, "ok"


_NOISE_TITLES = {"", "-", "youtube", "- youtube", "watch"}


def _clean_title(title):
    """A history row's page title is sometimes just the site name. Storing
    'YouTube' as a video title poisons search."""
    if not title:
        return None
    t = title.removesuffix(" - YouTube").strip()
    return None if t.lower() in _NOISE_TITLES else t


def parse_jsonl(body: bytes) -> list[dict]:
    out = []
    for line in body.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue                     # one bad line must not drop the batch
        vid = d.get("video_id") or extract_video_id(d.get("url", ""))
        if not vid or not VIDEO_ID.match(vid):
            continue
        ts = parse_iso(d.get("watched_at", ""))
        if ts is None:
            continue
        title = _clean_title(d.get("title"))
        out.append({
            "video_id": vid,
            "title": title,
            "channel": d.get("channel"),
            "watched_at": iso(ts),
            "dwell_s": d.get("dwell_s"),
            "source": d.get("source") or "unknown",
            "is_short": bool(d.get("is_short")) or "/shorts/" in (d.get("url") or ""),
        })
    return out


def parse_takeout(blob: bytes) -> list[dict]:
    """Google Takeout watch history. JSON first, HTML fallback."""
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = zf.namelist()
    js = [n for n in names if n.endswith("watch-history.json")]
    if js:
        return _takeout_json(json.loads(zf.read(js[0]).decode("utf-8", "replace")))
    hs = [n for n in names if n.endswith("watch-history.html")]
    if hs:
        return _takeout_html(zf.read(hs[0]).decode("utf-8", "replace"))
    return []


def _takeout_json(items) -> list[dict]:
    out = []
    for it in items:
        if not isinstance(it, dict) or it.get("header") != "YouTube":
            continue
        title = (it.get("title") or "")
        if not title.startswith("Watched "):
            continue                      # "Viewed", searched, browsed rows
        if any("Google Ads" in (d.get("name") or "") for d in (it.get("details") or [])):
            continue                      # ad impressions are titled "Watched ..." too
        url = it.get("titleUrl") or ""
        vid = extract_video_id(url)
        ts = parse_iso(it.get("time", ""))
        if not vid or ts is None:
            continue
        subs = it.get("subtitles") or [{}]
        out.append({
            "video_id": vid,
            "title": title[len("Watched "):].strip(),
            "channel": subs[0].get("name"),
            "watched_at": iso(ts),
            "dwell_s": None,              # Takeout carries no duration
            "source": "takeout",
            "is_short": bool(_SHORTS.search(url)),
        })
    return out


_A_HREF = re.compile(
    r'<a href="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"[^>]*>(.*?)</a>', re.DOTALL)
_DATE = re.compile(
    r"(\d{1,2}\.\s*\w+\.?\s*\d{4},\s*\d{1,2}:\d{2}:\d{2}[^<,]{0,6}"
    r"|[A-Z][a-z]{2} \d{1,2}, \d{4}, \d{1,2}:\d{2}:\d{2}\s*[AP]M[^<,]{0,6})")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DE_MONTHS = {m: i for i, m in enumerate(
    ["januar", "februar", "märz", "april", "mai", "juni", "juli",
     "august", "september", "oktober", "november", "dezember"], 1)}


def _takeout_html(doc: str) -> list[dict]:
    """Best effort. Google's locale-dependent date strings cannot all be parsed;
    rows whose date does not parse are dropped rather than guessed.
    ponytail: this shape is unverified against a real export - see Open Questions."""
    out = []
    for m in _A_HREF.finditer(doc):
        vid, raw_title = m.group(1), m.group(2)
        tail = doc[m.end():m.end() + 400]
        ts = _parse_takeout_date(tail)
        if ts is None:
            continue
        out.append({
            "video_id": vid,
            "title": htmllib.unescape(re.sub("<[^>]+>", "", raw_title)).strip(),
            "channel": None,
            "watched_at": iso(ts),
            "dwell_s": None,
            "source": "takeout",
            "is_short": False,
        })
    return out


def _parse_takeout_date(tail: str) -> datetime | None:
    tail = re.sub(r"<br\s*/?>", " ", tail)
    tail = htmllib.unescape(re.sub("<[^>]+>", " ", tail))
    m = _DATE.search(tail)
    if not m:
        return None
    text = " ".join(m.group(1).split())
    try:
        if "." in text.split(",")[0]:                       # German: 1. Mai 2023, 12:34:56 CEST
            day, month, year = re.match(r"(\d{1,2})\.\s*([^\s.]+)\.?\s*(\d{4})", text).groups()
            clock = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", text).groups()
            mo = _DE_MONTHS.get(month.lower())
            if mo is None:
                return None
            return datetime(int(year), mo, int(day), int(clock[0]),
                            int(clock[1]), int(clock[2]), tzinfo=UTC)
        m2 = re.match(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4}), (\d{1,2}):(\d{2}):(\d{2})\s*([AP]M)", text)
        if m2:
            mon, day, year, hh, mm, ss, ap = m2.groups()
            hh = int(hh) % 12 + (12 if ap == "PM" else 0)
            return datetime(int(year), _MONTHS[mon.lower()], int(day), hh,
                            int(mm), int(ss), tzinfo=UTC)
    except (AttributeError, ValueError):
        return None
    return None


def parse_url(url: str) -> list[dict]:
    vid = extract_video_id(url)
    if not vid:
        return []
    return [{"video_id": vid, "title": None, "channel": None,
             "watched_at": iso(datetime.now(UTC)), "dwell_s": None,
             "source": "url", "is_short": bool(_SHORTS.search(url))}]
