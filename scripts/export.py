#!/usr/bin/env python3
"""Write the archive out as a markdown tree any knowledge base can read.

  scripts/export.py                 -> data/export/<channel>/<date>-<slug>.md
  scripts/export.py --out ~/Obsidian/Vault/sources/youtube
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ytk import db, export


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--db", default=os.environ.get("YTK_DB", "data/ytk.sqlite"))
    a = ap.parse_args()
    out = a.out or os.environ.get("YTK_EXPORT") or "data/export"
    conn = db.connect(a.db)
    n = export.write_tree(conn, out)
    print(f"wrote {n} notes + registry.md -> {os.path.abspath(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
