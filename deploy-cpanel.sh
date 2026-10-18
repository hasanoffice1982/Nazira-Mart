#!/bin/bash
# Auto-deploy for cPanel. Run by cron every 5 minutes; does nothing unless GitHub has a new commit.
# Clone (git):  /home/salespal/narizmart       App (live): /home/salespal/narizmart_app
set -euo pipefail

CLONE=/home/salespal/narizmart
APP=/home/salespal/narizmart_app
BRANCH=main
LOG=/home/salespal/narizmart_data/deploy.log
LOCK=/home/salespal/narizmart_data/deploy.lock

mkdir -p "$(dirname "$LOG")" "$APP/tmp"
exec 9>"$LOCK"; flock -n 9 || exit 0          # never run two deploys at once
[ -f "$LOG" ] && [ "$(wc -l <"$LOG")" -gt 1000 ] && tail -n 500 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"   # keep the log short
exec >>"$LOG" 2>&1

cd "$CLONE"
git fetch --quiet origin "$BRANCH"
NEW=$(git rev-parse "origin/$BRANCH")
[ "$NEW" = "$(cat "$APP/.deployed" 2>/dev/null || true)" ] && exit 0

echo "== $(date -u +%FT%TZ) deploying $NEW"
OLD_REQ=$(cat "$APP/requirements-cpanel.txt" 2>/dev/null || true)
git reset --hard "origin/$BRANCH"               # the clone is deploy-only: never edit it by hand

# copy only what the live site needs (tests, docs, CI, sample data stay out)
for d in apps config templates static; do
  rm -rf "$APP/$d.new"
  cp -R "$d" "$APP/$d.new"                       # copy first, then swap, so files removed from the repo disappear too
  rm -rf "$APP/$d"
  mv "$APP/$d.new" "$APP/$d"
done
find "$APP" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
cp manage.py passenger_wsgi.py requirements-cpanel.txt "$APP/"

# the Python App's virtualenv (cPanel puts it under ~/virtualenv/<app root>/<python version>/)
PYVER=$(cat /home/salespal/narizmart_data/pyver 2>/dev/null || echo 3.12)   # must match the Python version chosen in Setup Python App
VENV=/home/salespal/virtualenv/narizmart_app/$PYVER/bin/activate
# shellcheck disable=SC1090
source "$VENV"
cd "$APP"
export DJANGO_SETTINGS_MODULE=config.settings.production

[ "$OLD_REQ" != "$(cat requirements-cpanel.txt)" ] && pip install --quiet -r requirements-cpanel.txt
python manage.py migrate --noinput
python manage.py collectstatic --noinput --verbosity 0

echo "$NEW" > .deployed
python manage.py check >/dev/null || { echo "ERROR: manage.py check failed, not restarting"; exit 1; }
touch tmp/restart.txt                            # Passenger restarts the app on the next request
echo "== done $NEW"
