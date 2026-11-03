#!/usr/bin/env python3
"""Backfill thumbnails. Safe to rerun: an existing file over 1 KiB is skipped."""
import os
import sys

from ytk import db, fetch

conn = db.connect(os.environ.get("YTK_DB", "/data/ytk.sqlite"))
thumbs = os.environ.get("YTK_THUMBS", "/data/thumbs")
rows = conn.execute(
    "SELECT id FROM videos WHERE state IN ('ready','fetched','no_caption','archived')").fetchall()
have = got = 0
for row in rows:
    vid = row["id"]
    if os.path.exists(os.path.join(thumbs, f"{vid}.jpg")):
        have += 1
        continue
    if fetch.thumbnail(vid, thumbs):
        got += 1
    print(f"  {vid} {'ok' if got else 'fail'}", flush=True)
print(f"{len(rows)} candidates, {have} already present, {got} fetched")
sys.exit(0 if got or have == len(rows) else 1)
