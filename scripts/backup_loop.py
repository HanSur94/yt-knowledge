#!/usr/bin/env python3
"""Daily snapshot of the archive, as a compose sidecar: no root, no cron.

sqlite3 .backup copies a consistent page set while the app is writing, which a
plain `cp` of a WAL database does not.
"""
import os
import sqlite3
import time
from datetime import date

SRC = os.environ.get("BACKUP_SRC", "/data/ytk.sqlite")
DEST = os.environ.get("BACKUP_DIR", "/backups")
INTERVAL = int(os.environ.get("BACKUP_INTERVAL", "86400"))
KEEP = int(os.environ.get("BACKUP_KEEP", "7"))


def snapshot() -> str:
    os.makedirs(DEST, exist_ok=True)
    path = os.path.join(DEST, f"ytk-{date.today().isoformat()}.sqlite")
    src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    dst = sqlite3.connect(path)
    src.backup(dst)
    dst.close()
    src.close()
    return path


def prune() -> list:
    files = sorted(f for f in os.listdir(DEST) if f.startswith("ytk-") and f.endswith(".sqlite"))
    gone = files[:-KEEP] if KEEP else []
    for f in gone:
        os.unlink(os.path.join(DEST, f))
    return gone


if __name__ == "__main__":
    while True:
        try:
            path = snapshot()
            gone = prune()
            size = os.path.getsize(path) // 1024
            print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {path} {size} KiB"
                  + (f" pruned {gone}" if gone else ""), flush=True)
        except Exception as exc:  # noqa: BLE001 - a backup failure must not kill the loop
            print(f"backup failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(INTERVAL)
