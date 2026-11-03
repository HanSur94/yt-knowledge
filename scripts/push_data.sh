#!/bin/sh
# Snapshot the archive to the NAS. The live database stays local: SQLite over a
# network share corrupts under concurrent writes, so this copies a checkpointed
# file instead of serving from the share.
set -e
cd "$(dirname "$0")/.."
NAS=${NAS:-Hannes@192.168.178.40}
DEST=${DEST:-/volume1/docker/yt-knowledge/data}
sqlite3 data/ytk.sqlite "PRAGMA wal_checkpoint(TRUNCATE);"
tar czf - -C data ytk.sqlite thumbs export 2>/dev/null \
  | ssh -i "$HOME/.ssh/id_nas" "$NAS" "mkdir -p $DEST && tar xzf - -C $DEST" 2>/dev/null
echo "synced $(du -sh data | cut -f1) to $NAS:$DEST"
