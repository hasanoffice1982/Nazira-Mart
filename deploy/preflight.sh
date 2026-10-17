#!/usr/bin/env bash
# READ-ONLY server inventory: changes nothing. Run it before bootstrap-server.sh, especially on a server that
# already hosts other apps, and share the output so the setup can be adapted. It prints no passwords or keys.
#
#   sudo bash deploy/preflight.sh
set -uo pipefail

hr() { printf '\n── %s ──\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
WARN=0
warn() { printf '  ⚠  %s\n' "$*"; WARN=1; }

hr "System"
# shellcheck source=/dev/null
. /etc/os-release 2>/dev/null && echo "OS: ${PRETTY_NAME:-unknown}"
echo "Kernel: $(uname -r)   CPUs: $(nproc)   Uptime: $(uptime -p 2>/dev/null || true)"
echo "Python: $(python3 --version 2>&1)"

hr "Memory and disk"
free -m | awk 'NR==1 || /Mem:|Swap:/'
AVAIL_MB="$(free -m | awk '/Mem:/ {print $7}')"
SWAP_MB="$(free -m | awk '/Swap:/ {print $2}')"
df -h / | awk 'NR==1 || NR==2'
FREE_GB="$(df -BG --output=avail / | tail -1 | tr -dc '0-9')"
[[ "${AVAIL_MB:-0}" -lt 1200 ]] && warn "less than 1.2 GB RAM available: use WEB_CONCURRENCY=2 and make sure there is swap"
[[ "${SWAP_MB:-0}" -eq 0 ]] && warn "no swap configured (bootstrap-server.sh will add 2 GB)"
[[ "${FREE_GB:-0}" -lt 5 ]] && warn "less than 5 GB free disk"
echo "Biggest memory users:"
ps -eo rss,comm --sort=-rss | head -8 | awk 'NR==1{print "  RSS(KB)  COMMAND"} NR>1{printf "  %-8s %s\n", $1, $2}'

hr "Listening ports (80, 443, 5432, 3306, 6379, 8000-8999)"
if have ss; then
  ss -ltnp 2>/dev/null | awk 'NR==1 || $4 ~ /:(80|443|5432|3306|6379|8[0-9][0-9][0-9])$/' | sed 's/users:((//; s/))//' | cut -c1-120
  for p in 80 443; do
    owner="$(ss -ltnp "( sport = :$p )" 2>/dev/null | grep -o 'users:(("[^"]*"' | head -1 | cut -d'"' -f2)"
    if [[ -n "$owner" && "$owner" != "nginx" ]]; then warn "port $p is used by '$owner', not nginx: narizmart's Nginx site would conflict"; fi
  done
fi

hr "Web servers and containers"
for svc in nginx apache2 httpd caddy traefik; do
  systemctl is-active --quiet "$svc" 2>/dev/null && echo "$svc: running"
done
have nginx && echo "nginx: $(nginx -v 2>&1)"
if have docker; then echo "docker containers:"; docker ps --format '  {{.Names}}  {{.Ports}}' 2>/dev/null | head -10; fi
if have nginx; then
  echo "Nginx sites in use (domains and listen ports only):"
  nginx -T 2>/dev/null | grep -E '^\s*(server_name|listen) ' | sed 's/^\s*/  /' | sort -u | head -30
  for f in /etc/nginx/sites-enabled/*; do [[ -e "$f" ]] && echo "  enabled: $(basename "$f")"; done
  [[ -f /etc/nginx/sites-enabled/narizmart ]] && echo "  (narizmart site is already installed)"
fi

hr "PostgreSQL"
if have psql; then
  echo "client: $(psql --version)"
  systemctl is-active --quiet postgresql 2>/dev/null && echo "system postgresql service: running" || echo "system postgresql service: not running"
  have pg_lsclusters && pg_lsclusters
else
  echo "psql not installed (bootstrap will install PostgreSQL)"
fi
have ss && ss -ltn 2>/dev/null | grep -q ':5432 ' && ! systemctl is-active --quiet postgresql 2>/dev/null && warn "port 5432 is in use but not by the system postgresql service (Docker?). Set DATABASE_URL yourself and run bootstrap with SKIP_DB=1"

hr "narizmart"
id narizmart >/dev/null 2>&1 && echo "user narizmart: exists" || echo "user narizmart: not created yet"
[[ -d /srv/narizmart ]] && echo "/srv/narizmart: exists" || echo "/srv/narizmart: not created yet"
[[ -f /etc/ssl/cloudflare/origin.pem ]] && echo "Cloudflare origin certificate: present" || echo "Cloudflare origin certificate: not saved yet"

hr "Verdict"
if [[ "$WARN" == 0 ]]; then echo "  No blocking problems found."; else echo "  Review the ⚠ lines above before running bootstrap-server.sh."; fi
