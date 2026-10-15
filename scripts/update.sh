#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if docker info >/dev/null 2>&1; then
  dc() { docker compose "$@"; }
else
  dc() { sudo docker compose "$@"; }
fi
mkdir -p backups
chmod 700 backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
dc exec -T -e BACKGROUND_WORKER=0 app python -c "from app import ledger; ledger.backup('/data/pre-update.sqlite3')"
dc cp app:/data/pre-update.sqlite3 "backups/$stamp.sqlite3"
git pull --ff-only
dc up -d --build --wait
printf 'Update complete. Backup: backups/%s.sqlite3\n' "$stamp"
