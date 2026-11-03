#!/usr/bin/env python3
"""Headless backfill. No HTTP server. Bounded by time or row count, safe to
re-run, and it rides out an IP block instead of burning attempts on it.

  scripts/backfill.py --hours 3            # run up to 3 hours
  scripts/backfill.py --limit 50 --sleep 8
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ytk import db, worker
from ytk.config import load


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--limit", type=int, default=0, help="stop after N rows")
    ap.add_argument("--sleep", type=float, default=8.0,
                    help="seconds between videos; the CDN blocks around 25 requests")
    a = ap.parse_args()

    os.environ.setdefault("YTK_SLEEP", str(a.sleep))
    cfg = load()
    conn = db.connect(cfg.db, same_thread=False)
    db.resume_stuck(conn)

    started, done, blocked = time.time(), 0, 0
    deadline = started + a.hours * 3600
    while time.time() < deadline:
        if a.limit and done >= a.limit:
            break
        try:
            got = worker._tick(conn, cfg)
        except Exception as exc:  # noqa: BLE001 - keep going on a bad row
            print(f"tick error: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(30)
            continue
        if got is None:
            print("queue empty", flush=True)
            break
        if isinstance(got, str) and got.startswith("blocked"):
            blocked += 1
            wait = min(worker.BLOCK_PAUSE, max(1, deadline - time.time()))
            print(f"  blocked ({got}) - waiting {int(wait)}s", flush=True)
            time.sleep(wait)
            continue
        done += 1
        c = db.queue_counts(conn)
        rate = done / max(1e-9, (time.time() - started)) * 60
        print(f"  {done:5d} done  {rate:5.1f}/min  "
              f"pending={c['pending']} fetched={c['fetched']} failed={c['failed']} "
              f"nocap={c['no_caption']} arch={c['archived']}", flush=True)
        time.sleep(a.sleep)

    c = db.queue_counts(conn)
    print(f"\nfinished: {done} rows in {(time.time()-started)/60:.1f} min, "
          f"{blocked} block pauses\n{ {k: c[k] for k in ('pending','fetched','failed','no_caption','archived')} }")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
