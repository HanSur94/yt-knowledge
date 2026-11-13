#!/bin/sh
# Push Mac browser history to the archive. The token lives in the Keychain,
# not in this file: it is write-scoped to /import and cannot log anyone in.
set -e
cd "$(dirname "$0")/.."
TOKEN=$(security find-generic-password -s ytk-import -w)
export YTK_IMPORT_TOKEN="$TOKEN"
exec .venv/bin/python -m ytk.collector --push "${YTK_URL:-http://192.168.178.40:8499}"
