#!/bin/sh
# Daily PostgreSQL backup (runs inside the `backup` container). Custom-format dumps are compressed and restorable
# with pg_restore. Keeps BACKUP_KEEP_DAYS days (default 14). One-shot mode for testing: BACKUP_ONCE=1
set -u
KEEP="${BACKUP_KEEP_DAYS:-14}"
DIR="${BACKUP_DIR:-/backups}"
mkdir -p "$DIR"
echo "backup service started: every ${BACKUP_INTERVAL_SECONDS:-86400}s, keeping ${KEEP} days in ${DIR}"

while true; do
  f="$DIR/market-$(date -u +%Y%m%d-%H%M%S).dump"
  if pg_dump -Fc -f "$f.part"; then
    mv "$f.part" "$f"
    echo "$(date -u +%FT%TZ) backup ok: $f ($(du -h "$f" | cut -f1))"
  else
    rm -f "$f.part"
    echo "$(date -u +%FT%TZ) BACKUP FAILED" >&2
  fi
  find "$DIR" -name 'market-*.dump' -mtime +"$KEEP" -delete
  [ "${BACKUP_ONCE:-0}" = "1" ] && exit 0
  sleep "${BACKUP_INTERVAL_SECONDS:-86400}"
done