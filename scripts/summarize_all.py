#!/usr/bin/env python3
"""Summarise every fetched video that has a transcript and no summary.
No YouTube traffic: works entirely from stored segments.

  YTK_LLM_BASE=http://nas:8642/v1 YTK_LLM_MODEL=auto YTK_LLM_KEY=... scripts/summarize_all.py
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ytk import db, summarize

QUERY = """
SELECT v.* FROM videos v
WHERE v.state IN ('fetched','failed')   -- failed-at-summarize rows still have a transcript
  AND EXISTS (SELECT 1 FROM segments s WHERE s.video_id = v.id)
  AND NOT EXISTS (SELECT 1 FROM summaries m WHERE m.video_id = v.id)
ORDER BY (SELECT COUNT(*) FROM watches w WHERE w.video_id=v.id) DESC,
         (SELECT COALESCE(MAX(dwell_s),0) FROM watches w WHERE w.video_id=v.id) DESC
LIMIT ?
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("YTK_DB", "data/ytk.sqlite"))
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--model", default=os.environ.get("YTK_LLM_MODEL", "auto"))
    a = ap.parse_args()
    if not os.environ.get("YTK_LLM_BASE"):
        print("YTK_LLM_BASE is not set", file=sys.stderr)
        return 2

    conn = db.connect(a.db)
    rows = conn.execute(QUERY, (a.limit,)).fetchall()
    print(f"{len(rows)} to summarise", flush=True)
    ok = bad = 0
    for i, row in enumerate(rows, 1):
        vid = row["id"]
        segs = [(r[0], r[1]) for r in conn.execute(
            "SELECT start_s, text FROM segments WHERE video_id=? ORDER BY start_s", (vid,))]
        chaps = [(r[0], r[1]) for r in conn.execute(
            "SELECT start_s, title FROM chapters WHERE video_id=? ORDER BY start_s", (vid,))]
        skips = [(r[0], r[1], r[2]) for r in conn.execute(
            "SELECT start_s, end_s, category FROM skips WHERE video_id=?", (vid,))]
        t0 = time.time()
        try:
            s = summarize.summarize(dict(row), segs, chaps, skips)
        except Exception as exc:  # noqa: BLE001 - one video must not stop the run
            bad += 1
            print(f"  {i:3d} {vid} FAILED {type(exc).__name__}: {str(exc)[:80]}", flush=True)
            continue
        s["title"], s["channel"] = row["title"], row["channel"]
        s["model"] = a.model
        db.save_summary(conn, vid, s)
        ok += 1
        print(f"  {i:3d} {time.time()-t0:4.1f}s  {s['headline'][:70]}  "
              f"[{len(s['keywords'])} kw, {len(s['tags'])} tags]", flush=True)
    print(f"\n{ok} summarised, {bad} failed")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
