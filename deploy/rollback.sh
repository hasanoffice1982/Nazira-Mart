#!/usr/bin/env bash
# Manual rollback: switch back to an earlier release and restart.
#   rollback.sh                 the most recent successfully deployed release that is not live
#   rollback.sh v1.2.0          a release by its version label (as listed in shared/deploy-history)
#   rollback.sh 6476069         a release by (the start of) its commit id
# Does NOT undo database migrations (see the note in deploy.sh); restore a pre-deploy backup in shared/backups if you need that.
set -euo pipefail
BASE="${DEPLOY_BASE:-/srv/narizmart}"
SERVICE="${DEPLOY_SERVICE:-narizmart.service}"
SHARED="$BASE/shared"
HISTORY="$SHARED/deploy-history"
CURRENT="$(readlink -f "$BASE/current")"

history_newest_first() { awk '{a[NR]=$0} END{for(i=NR;i>0;i--) print a[i]}' "$HISTORY" 2>/dev/null || true; }

TARGET=""
WANT="${1:-}"
while read -r sha label; do
  [[ -n "$sha" && -d "$BASE/releases/$sha" ]] || continue          # its files are already gone
  [[ "$(readlink -f "$BASE/releases/$sha")" != "$CURRENT" ]] || continue   # that is the live one
  if [[ -z "$WANT" || "$label" == "$WANT" || "$sha" == "$WANT"* ]]; then TARGET="$BASE/releases/$sha"; break; fi
done < <(history_newest_first)
[[ -d "$TARGET" ]] || { echo "no matching earlier release to roll back to${WANT:+: $WANT}" >&2; echo "available:" >&2; history_newest_first | awk '{print "  " $2 "  (" substr($1,1,7) ")"}' >&2; exit 1; }

echo "live:   $(basename "$CURRENT")"
echo "target: $(basename "$TARGET")  ${WANT:+($WANT)}"
ln -sfn "$TARGET" "$BASE/current.new" && mv -Tf "$BASE/current.new" "$BASE/current"
sha="$(cat "$TARGET/.version" 2>/dev/null || basename "$TARGET")"
label="$(cat "$TARGET/.release" 2>/dev/null || echo "${sha:0:7}")"
printf 'APP_VERSION=%s\nAPP_RELEASE=%s\n' "$sha" "$label" > "$SHARED/version.env"
sudo -n systemctl reset-failed "$SERVICE" 2>/dev/null || true
sudo -n systemctl restart "$SERVICE"
echo "rolled back to $label"
