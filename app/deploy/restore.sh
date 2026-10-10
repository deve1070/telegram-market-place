#!/usr/bin/env bash
# Restore the database from a backup file:   bash deploy/restore.sh backups/market-20261105-020000.dump
# This REPLACES the current data. Run it from the project folder on the server.
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:?usage: bash deploy/restore.sh backups/<file>.dump}"
[ -f "$FILE" ] || { echo "no such file: $FILE"; exit 1; }
COMPOSE="docker compose -f docker-compose.prod.yml"

read -r -p "This will OVERWRITE the live database with $FILE. Type YES to continue: " ok
[ "$ok" = "YES" ] || { echo "aborted"; exit 1; }

echo "→ safety copy of the current database first"
$COMPOSE exec -T db pg_dump -U market -Fc market > "backups/before-restore-$(date -u +%Y%m%d-%H%M%S).dump"

echo "→ stopping the bot"
$COMPOSE stop bot

echo "→ restoring"
$COMPOSE exec -T db pg_restore -U market -d market --clean --if-exists --no-owner < "$FILE"

echo "→ starting the bot"
$COMPOSE start bot
echo "done"