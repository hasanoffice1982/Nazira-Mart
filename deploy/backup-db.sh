#!/usr/bin/env bash
# Nightly PostgreSQL backup (run by narizmart-backup.timer). Reads DATABASE_URL from the environment, which systemd
# fills from shared/.env. Keeps the newest $KEEP_BACKUPS dumps (default 14). Dumps are private (mode 600).
#
# Restore:  gunzip -c backups/nightly-<time>.sql.gz | psql "$DATABASE_URL"      (into an empty database)
# Off-server copies are strongly recommended: these files live on the same disk as the database.
set -euo pipefail

BASE="${DEPLOY_BASE:-/srv/narizmart}"
DIR="$BASE/shared/backups"
KEEP="${KEEP_BACKUPS:-14}"
[[ -n "${DATABASE_URL:-}" ]] || { echo "DATABASE_URL is not set" >&2; exit 1; }
[[ "$DATABASE_URL" == postgres* ]] || { echo "not a PostgreSQL database, nothing to back up"; exit 0; }

mkdir -p "$DIR"
umask 077
OUT="$DIR/nightly-$(date +%Y%m%d-%H%M%S).sql.gz"
trap 'rm -f "$OUT.partial"' EXIT   # never leave a half-written dump behind
pg_dump --no-owner --dbname="$DATABASE_URL" | gzip > "$OUT.partial"
gzip -t "$OUT.partial"                       # the archive must be readable
[[ "$(stat -c %s "$OUT.partial" 2>/dev/null || stat -f %z "$OUT.partial")" -gt 200 ]] || { echo "backup suspiciously small" >&2; rm -f "$OUT.partial"; exit 1; }
mv "$OUT.partial" "$OUT"
echo "saved $OUT ($(du -h "$OUT" | cut -f1))"

# keep the newest $KEEP nightly dumps (names are generated above, so ls is safe here)
# shellcheck disable=SC2012
ls -1t "$DIR"/nightly-*.sql.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
