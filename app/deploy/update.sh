#!/usr/bin/env bash
# Deploy new code: backup, rebuild, restart.   Run on the server, in the project folder:   bash deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml"
mkdir -p backups

echo "→ backup before update"
$COMPOSE exec -T db pg_dump -U market -Fc market > "backups/pre-update-$(date -u +%Y%m%d-%H%M%S).dump"

echo "→ building and restarting"
$COMPOSE build bot
$COMPOSE up -d

echo "→ waiting for the bot to become healthy"
cid=$($COMPOSE ps -q bot)
for _ in $(seq 1 30); do
  state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo unknown)
  if [ "$state" = "healthy" ]; then echo "bot is healthy"; $COMPOSE ps; exit 0; fi
  sleep 4
done
echo "bot did not become healthy in time. Check:  $COMPOSE logs --tail 80 bot"
exit 1