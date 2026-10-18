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
  rsync -a --delete --exclude '__pycache__' "$d/" "$APP/$d/"   # --delete drops files removed from the repo
done
cp manage.py passenger_wsgi.py requirements-cpanel.txt "$APP/"

# the Python App's virtualenv (cPanel puts it under ~/virtualenv/<app root>/<python version>/)
VENV=$(ls -d /home/salespal/virtualenv/narizmart_app/*/bin/activate | head -1)
# shellcheck disable=SC1090
source "$VENV"
cd "$APP"
export DJANGO_SETTINGS_MODULE=config.settings.production

[ "$OLD_REQ" != "$(cat requirements-cpanel.txt)" ] && pip install --quiet -r requirements-cpanel.txt
python manage.py migrate --noinput
python manage.py collectstatic --noinput --verbosity 0

echo "$NEW" > .deployed
touch tmp/restart.txt                            # Passenger restarts the app on the next request
echo "== done $NEW"
