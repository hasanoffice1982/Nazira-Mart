#!/usr/bin/env bash
# Creates the app's PostgreSQL role and database and writes DATABASE_URL into the .env. Safe to run again: anything
# that already exists is left alone and the password is never changed unless you ask (RESET_PASSWORD=1).
#
#   sudo bash deploy/setup-database.sh
#
# Shared-server friendly: it only creates the narizmart role/database and revokes public access to the new database,
# so other apps' database users cannot read narizmart's data. It never touches any other database.
# Environment: BASE DB_NAME DB_ROLE DB_HOST DB_PORT RESET_PASSWORD  (PG_ADMIN_PSQL is for testing: the psql command to use)
set -euo pipefail

BASE="${BASE:-/srv/narizmart}"
DB_NAME="${DB_NAME:-narizmart}"
DB_ROLE="${DB_ROLE:-narizmart}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
ENV_FILE="$BASE/shared/.env"
read -r -a PSQL <<< "${PG_ADMIN_PSQL:-sudo -u postgres psql}"

die() { echo "ERROR: $*" >&2; exit 1; }
[[ "$DB_NAME" =~ ^[a-z_][a-z0-9_]*$ && "$DB_ROLE" =~ ^[a-z_][a-z0-9_]*$ ]] || die "DB_NAME/DB_ROLE may only contain a-z, 0-9 and _"
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE does not exist yet (bootstrap-server.sh creates it)"
q() { "${PSQL[@]}" -v ON_ERROR_STOP=1 -qtAX "$@"; }

current_url() { grep -E '^DATABASE_URL=.+' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
set_url() {
  local url="$1" tmp
  tmp="$(mktemp)"
  grep -vE '^DATABASE_URL=' "$ENV_FILE" > "$tmp" || true
  echo "DATABASE_URL=$url" >> "$tmp"
  cat "$tmp" > "$ENV_FILE"   # keep the file's owner and permissions
  rm -f "$tmp"
}

echo "==> PostgreSQL: $(q -c 'SHOW server_version')"

ROLE_EXISTS="$(q -c "SELECT 1 FROM pg_roles WHERE rolname='$DB_ROLE'")"
if [[ -z "$ROLE_EXISTS" ]]; then
  PASS="$(openssl rand -hex 24)"
  q -c "CREATE ROLE \"$DB_ROLE\" LOGIN PASSWORD '$PASS'"
  echo "created role $DB_ROLE"
  set_url "postgres://$DB_ROLE:$PASS@$DB_HOST:$DB_PORT/$DB_NAME"
elif [[ "${RESET_PASSWORD:-0}" == 1 ]]; then
  PASS="$(openssl rand -hex 24)"
  q -c "ALTER ROLE \"$DB_ROLE\" PASSWORD '$PASS'"
  echo "reset the password of role $DB_ROLE"
  set_url "postgres://$DB_ROLE:$PASS@$DB_HOST:$DB_PORT/$DB_NAME"
elif [[ -z "$(current_url)" ]]; then
  die "role $DB_ROLE exists but $ENV_FILE has no DATABASE_URL (its password is unknown). Re-run with RESET_PASSWORD=1 to set a new one."
else
  echo "role $DB_ROLE already exists, keeping its password"
fi

if [[ -z "$(q -c "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")" ]]; then
  q -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_ROLE\" ENCODING 'UTF8' TEMPLATE template0 LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8'" 2>/dev/null \
    || q -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_ROLE\" ENCODING 'UTF8'"
  echo "created database $DB_NAME"
else
  echo "database $DB_NAME already exists"
fi

# Django's recommended connection defaults, and isolation from every other role on this server
q -c "ALTER ROLE \"$DB_ROLE\" SET client_encoding TO 'utf8'"
q -c "ALTER ROLE \"$DB_ROLE\" SET default_transaction_isolation TO 'read committed'"
q -c "ALTER ROLE \"$DB_ROLE\" SET timezone TO 'UTC'"
q -c "REVOKE ALL ON DATABASE \"$DB_NAME\" FROM PUBLIC"
q -c "GRANT ALL ON DATABASE \"$DB_NAME\" TO \"$DB_ROLE\""

URL="$(current_url)"
[[ -n "$URL" ]] || die "DATABASE_URL missing from $ENV_FILE"
echo "==> DATABASE_URL is set in $ENV_FILE"
if command -v psql >/dev/null && psql "$URL" -qtAc "SELECT 'connection ok as ' || current_user" 2>/dev/null; then :; else
  echo "note: could not test the login from this shell (the web app will use the same URL)"
fi
