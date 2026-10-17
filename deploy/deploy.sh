#!/usr/bin/env bash
# Release script — runs ON THE SERVER as the app user (invoked over SSH by .github/workflows/deploy.yml).
#
#   deploy.sh <git-sha> [release-label]      label example: v1.2.0 (default: the first 7 characters of the commit)
#
# Layout under $BASE (default /srv/narizmart):
#   releases/<sha>/   one directory per release (kept: newest $KEEP_RELEASES)
#   venvs/<hash>/     virtualenv per requirements.txt hash, shared by releases with identical dependencies
#   shared/           .env, media/, static/, backups/, version.env  (never replaced by a release)
#   current           symlink to the live release
#
# Steps: build venv -> checks -> collectstatic -> database backup -> migrate -> switch -> restart -> health check.
# If anything fails after the switch, the previous release is restored automatically.
# Migrations are NOT undone on rollback, so keep them backward compatible (add columns/tables first, remove later).
set -euo pipefail

SHA="${1:?usage: deploy.sh <git-sha> [release-label]}"
LABEL="${2:-${SHA:0:7}}"
BASE="${DEPLOY_BASE:-/srv/narizmart}"
KEEP="${KEEP_RELEASES:-3}"
SERVICE="${DEPLOY_SERVICE:-narizmart.service}"
SOCKET="${DEPLOY_SOCKET:-/run/narizmart/gunicorn.sock}"
RELEASE="$BASE/releases/$SHA"
SHARED="$BASE/shared"

log() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "$SHA" =~ ^[0-9a-f]{7,40}$ ]] || die "invalid release id: $SHA"
[[ "$LABEL" =~ ^[A-Za-z0-9._+-]{1,60}$ ]] || die "invalid release label: $LABEL"
[[ -d "$RELEASE" ]] || die "release directory missing: $RELEASE"
[[ -f "$SHARED/.env" ]] || die "missing $SHARED/.env (run deploy/bootstrap-server.sh first)"
command -v flock >/dev/null || die "flock is required"

mkdir -p "$BASE/venvs" "$SHARED/backups" "$SHARED/media" "$SHARED/static"
exec 9>"$BASE/.deploy.lock"
flock -n 9 || die "another deploy is already running"

# The release that is live right now. NOT simply `readlink -f current`: when `current` does not exist yet (first deploy)
# that prints the path of `current` itself, which would later be mistaken for a release to roll back to.
PREVIOUS=""
if [[ -L "$BASE/current" ]]; then
  PREVIOUS="$(readlink -f "$BASE/current" 2>/dev/null || true)"
  [[ -n "$PREVIOUS" && -d "$PREVIOUS" && "$PREVIOUS" != "$BASE/current" ]] || PREVIOUS=""
fi
SWITCHED=0

# version.env feeds the running service: APP_VERSION (exact commit, also versions the service worker) and APP_RELEASE (label)
write_version_env() {
  local dir="$1" sha label
  sha="$(cat "$dir/.version" 2>/dev/null || basename "$dir")"
  label="$(cat "$dir/.release" 2>/dev/null || echo "${sha:0:7}")"
  printf 'APP_VERSION=%s\nAPP_RELEASE=%s\n' "$sha" "$label" > "$SHARED/version.env"
}

restart() {
  # reset-failed clears systemd's start-rate limit, which a previous crash loop could leave blocking new starts
  sudo -n systemctl reset-failed "$SERVICE" 2>/dev/null || true
  sudo -n systemctl restart "$SERVICE"
}

health() {
  # Talks to gunicorn through its unix socket, the same way Nginx does.
  curl -fsS --max-time 5 --unix-socket "$SOCKET" \
    -H "Host: ${HEALTH_HOST:-localhost}" -H "X-Forwarded-Proto: https" -H "X-Forwarded-For: 127.0.0.1" \
    "http://localhost/healthz"
}

# Runs once, in the main shell, whenever the script exits (success or failure). Subshells such as $(...) never run it,
# so a single failed health probe while the service is still starting cannot trigger a rollback.
on_exit() {
  local code=$?
  trap - EXIT
  [[ "$code" -eq 0 ]] && return 0
  printf '\nERROR: deploy failed (exit %s).\n' "$code" >&2
  if [[ "$SWITCHED" == 1 ]]; then
    echo "--- last lines of the service log ($SERVICE):" >&2
    journalctl -u "$SERVICE" -n 25 --no-pager -o cat 2>&1 | sed 's/^/    /' >&2 || true
  fi
  if [[ "$SWITCHED" == 1 && -n "$PREVIOUS" && -d "$PREVIOUS" ]]; then
    log "Rolling back to $(basename "$PREVIOUS")"
    if ln -sfn "$PREVIOUS" "$BASE/current.new" && mv -Tf "$BASE/current.new" "$BASE/current"; then
      :
    else
      echo "WARNING: could not switch back to $PREVIOUS" >&2
    fi
    write_version_env "$PREVIOUS"
    restart || true
    printf 'Rolled back to %s. The database was already migrated; a pre-deploy backup is in %s/backups.\n' "$(basename "$PREVIOUS")" "$SHARED" >&2
  elif [[ "$SWITCHED" == 1 ]]; then
    printf 'There was no previous release to roll back to.\n' >&2
  else
    printf 'The live release was not touched.\n' >&2
  fi
  # a release that never went live is just clutter; the live and previous releases are never touched here
  if [[ "$(readlink -f "$BASE/current" 2>/dev/null)" != "$(readlink -f "$RELEASE")" ]]; then
    rm -rf "$RELEASE"
  fi
}
trap on_exit EXIT

cd "$RELEASE"
echo "$SHA" > .version
echo "$LABEL" > .release

log "Python environment"
REQ_HASH="$(sha256sum requirements.txt | cut -c1-16)"
VENV="$BASE/venvs/$REQ_HASH"
# Built in place (never renamed afterwards: console scripts such as gunicorn embed the interpreter path).
# The marker file means "installed completely", so a half-built environment is rebuilt, not reused.
if [[ ! -f "$VENV/.complete" ]]; then
  rm -rf "$VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --no-cache-dir --upgrade pip
  "$VENV/bin/pip" install --quiet --no-cache-dir -r requirements.txt
  touch "$VENV/.complete"
else
  echo "reusing $VENV"
fi
ln -sfn "$VENV" "$RELEASE/.venv"
ln -sfn "$SHARED/.env" "$RELEASE/.env"   # django-environ reads BASE_DIR/.env
PY="$VENV/bin/python"

export DJANGO_SETTINGS_MODULE=config.settings.production
export APP_VERSION="$SHA"
export APP_RELEASE="$LABEL"

log "Django checks"
"$PY" manage.py check --deploy --fail-level ERROR
HEALTH_HOST="$("$PY" manage.py shell -c 'from django.conf import settings; print(settings.ALLOWED_HOSTS[0])' 2>/dev/null | tail -1)"
export HEALTH_HOST

log "Static files"
"$PY" manage.py collectstatic --noinput --verbosity 0

log "Database backup"
DB_URL="$("$PY" manage.py shell -c "import os; print(os.environ.get('DATABASE_URL', ''))" 2>/dev/null | tail -1)"
if [[ "$DB_URL" == postgres* ]] && command -v pg_dump >/dev/null; then
  BACKUP="$SHARED/backups/pre-$SHA-$(date +%Y%m%d-%H%M%S).sql.gz"
  pg_dump --no-owner --dbname="$DB_URL" | gzip > "$BACKUP"
  chmod 600 "$BACKUP"
  echo "saved $BACKUP ($(du -h "$BACKUP" | cut -f1))"
  # keep the 10 newest backups (names are generated by this script, so ls is safe here)
  # shellcheck disable=SC2012
  ls -1t "$SHARED"/backups/pre-*.sql.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
else
  echo "skipped (no PostgreSQL DATABASE_URL or pg_dump not installed)"
fi

log "Migrations"
"$PY" manage.py migrate --noinput

log "Switching to $SHA"
ln -sfn "$RELEASE" "$BASE/current.new"
mv -Tf "$BASE/current.new" "$BASE/current"
SWITCHED=1
write_version_env "$RELEASE"
restart

log "Health check"
for attempt in $(seq 1 30); do
  if body="$(health 2>/dev/null)"; then echo "healthy: $body"; break; fi
  [[ "$attempt" == 30 ]] && die "service did not become healthy within 60s"
  sleep 2
done

echo "$SHA $LABEL" >> "$SHARED/deploy-history"   # successful deploys only (commit + label); rollback.sh reads this
tail -n 50 "$SHARED/deploy-history" > "$SHARED/deploy-history.tmp" && mv -f "$SHARED/deploy-history.tmp" "$SHARED/deploy-history"

log "Cleaning up old releases"
# release directory names are validated hex ids, so ls is safe here
# shellcheck disable=SC2012
ls -1dt "$BASE"/releases/*/ 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
  old="${old%/}"
  [[ "$(readlink -f "$old")" == "$(readlink -f "$BASE/current")" ]] && continue
  echo "removing $old"; rm -rf "$old"
done
# drop virtualenvs no remaining release points at
for v in "$BASE"/venvs/*/; do
  v="${v%/}"
  in_use=0
  for rel in "$BASE"/releases/*/; do
    [[ "$(readlink -f "${rel}.venv" 2>/dev/null)" == "$(readlink -f "$v")" ]] && in_use=1 && break
  done
  [[ "$in_use" == 0 ]] && { echo "removing unused venv $v"; rm -rf "$v"; }
done

log "Deployed $LABEL ($SHA)"
