"""Runs on the user's machine, never in the container.

Reads browser history databases directly. No third-party browser-history
library: `browser-history` 0.5.0 reported every timestamp one hour late
(delta exactly 3600 s on 4,976 rows), which would have shifted the whole
archive. Each browser's epoch is handled here, in the open.
"""
import argparse
import glob
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime

from .ingest import extract_video_id, iso

MAC = "~/Library/Application Support"
CHROMIUM = [f"{MAC}/Google/Chrome/*/History",
            f"{MAC}/Arc/User Data/*/History",
            f"{MAC}/BraveSoftware/Brave-Browser/*/History",
            f"{MAC}/Microsoft Edge/*/History",
            f"{MAC}/Vivaldi/*/History",
            "~/.config/google-chrome/*/History",
            "~/.config/chromium/*/History",
            "~/AppData/Local/Google/Chrome/User Data/*/History"]
FIREFOX = [f"{MAC}/Firefox/Profiles/*/places.sqlite",
           "~/.mozilla/firefox/*/places.sqlite"]
SAFARI = ["~/Library/Safari/History.db"]   # not under Application Support

WEBKIT_EPOCH = 978307200      # 2001-01-01 -> 1970-01-01, seconds
CHROMIUM_EPOCH = 11644473600  # 1601-01-01 -> 1970-01-01, seconds


def _read(path: str, sql: str):
    """Live databases are locked; query a copy."""
    tmp = tempfile.mktemp(suffix=os.path.basename(path))
    try:
        shutil.copy2(os.path.expanduser(path), tmp)
        conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def chromium_rows():
    for pattern in CHROMIUM:
        for db in glob.glob(os.path.expanduser(pattern)):
            name = "arc" if "/Arc/" in db else "chrome"
            for url, title, vt, dur in _read(db, """
                    SELECT u.url, u.title, v.visit_time, v.visit_duration
                    FROM visits v JOIN urls u ON u.id = v.url
                    WHERE u.url LIKE '%youtube.com/%'"""):
                yield (url, vt / 1_000_000 - CHROMIUM_EPOCH,
                       (round(dur / 1_000_000) if dur else None), name, title)


def firefox_rows():
    for db in glob.glob(os.path.expanduser(FIREFOX[0])) + glob.glob(os.path.expanduser(FIREFOX[1])):
        for url, title, vt in _read(db, """
                SELECT p.url, p.title, h.visit_date FROM moz_historyvisits h
                JOIN moz_places p ON p.id = h.place_id
                WHERE p.url LIKE '%youtube.com/%'"""):
            yield url, vt / 1_000_000, None, "firefox", title


def safari_rows():
    for db in SAFARI:
        if not os.path.exists(os.path.expanduser(db)):
            continue
        for url, title, vt in _read(db, """
                SELECT i.url, v.title, v.visit_time FROM history_visits v
                JOIN history_items i ON i.id = v.history_item
                WHERE i.url LIKE '%youtube.com/%'"""):
            yield url, vt + WEBKIT_EPOCH, None, "safari", title


def collect() -> list:
    seen = {}
    for adapter in (safari_rows, chromium_rows, firefox_rows):
        for url, ts, dwell, source, title in adapter():
            if "/watch" not in url:
                continue                      # shorts and channels are not watches
            vid = extract_video_id(url)
            if not vid:
                continue
            key = (vid, round(ts))
            if key in seen and seen[key]["dwell_s"] is not None:
                continue                      # keep the copy that has dwell
            seen[key] = {
                "video_id": vid,
                "title": (title or None),
                "channel": None,
                "watched_at": iso(datetime.fromtimestamp(ts, UTC)),
                "dwell_s": dwell,
                "source": source,
                "is_short": False,
            }
    return sorted(seen.values(), key=lambda r: r["watched_at"])


def summarise(rows: list) -> dict:
    return {"visits": len(rows),
            "distinct": len({r["video_id"] for r in rows}),
            "with_dwell": sum(1 for r in rows if r["dwell_s"] is not None),
            "span": (rows[0]["watched_at"], rows[-1]["watched_at"]) if rows else None}


def push(rows: list, url: str, token: str, batch: int = 500) -> dict:
    import urllib.request
    total = {"received": 0, "new": 0, "merged": 0, "rejected": 0}
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        body = "\n".join(json.dumps(r) for r in chunk).encode()
        req = urllib.request.Request(
            url.rstrip("/") + "/import", data=body, method="POST",
            headers={"Content-Type": "application/x-ndjson",
                     "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            got = json.loads(resp.read())
        for k in total:
            total[k] += got.get(k, 0)
    return total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ytk collector")
    ap.add_argument("--push", metavar="URL", help="base URL of the service")
    ap.add_argument("--token", default=os.environ.get("YTK_SECRET"))
    ap.add_argument("--dry-run", action="store_true", default=True)
    a = ap.parse_args(argv)
    rows = collect()
    print(json.dumps(summarise(rows), indent=2))
    if not a.push:
        print("dry run: nothing sent")
        return 0
    if not a.token:
        print("missing --token or YTK_SECRET", file=sys.stderr)
        return 2
    print(json.dumps(push(rows, a.push, a.token), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
