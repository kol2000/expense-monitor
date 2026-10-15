#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p backups
chmod 700 backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T -e BACKGROUND_WORKER=0 app python -c "from app import ledger; ledger.backup('/data/pre-update.sqlite3')"
docker compose cp app:/data/pre-update.sqlite3 "backups/$stamp.sqlite3"
git pull --ff-only
docker compose up -d --build --wait
printf 'Update complete. Backup: backups/%s.sqlite3\n' "$stamp"
