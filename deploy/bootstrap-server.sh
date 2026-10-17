#!/usr/bin/env bash
# One-time server setup for Ubuntu 22.04/24.04. Run as root from a checkout of this repository:
#
#   sudo bash deploy/bootstrap-server.sh example.com
#
# Installs Nginx, PostgreSQL and Python tooling; creates the `narizmart` user, directories, database, systemd units,
# Nginx site and a first .env. Safe to re-run: existing .env, database and certificates are left alone.
# It does NOT enable a firewall or issue the TLS certificate; the next steps are printed at the end.
set -Eeuo pipefail

DOMAIN="${1:?usage: bootstrap-server.sh <domain>   e.g. example.com}"
[[ "$EUID" -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE=/srv/narizmart
APP_USER=narizmart

# Shared server? Look first: sudo bash deploy/preflight.sh   (read-only). This script never removes other apps' Nginx sites,
# never restarts Nginx (reload only, after a successful config test), and never touches existing databases.
echo "==> Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# only what we need (Pillow ships its own image libraries); packages that are already installed are left as they are
apt-get install -y -qq nginx postgresql-client python3 python3-venv python3-pip curl openssl > /dev/null

echo "==> User and directories"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "$APP_USER"
install -d -o "$APP_USER" -g www-data -m 0750 "$BASE" "$BASE/releases" "$BASE/venvs" "$BASE/incoming"
install -d -o "$APP_USER" -g www-data -m 0750 "$BASE/shared" "$BASE/shared/backups"
install -d -o "$APP_USER" -g www-data -m 0755 "$BASE/shared/media" "$BASE/shared/static"
chmod 0711 "$BASE"          # Nginx (www-data) may traverse to shared/static and shared/media only
chmod 0755 "$BASE/shared"   # .env stays 0600 below
install -d -m 0755 /var/www/certbot

echo "==> Swap (this server has little RAM; swap prevents out-of-memory kills during deploys)"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "created 2G /swapfile"
else
  echo "swap already present"
fi

echo "==> PostgreSQL"
if [[ "${SKIP_DB:-0}" == 1 ]]; then
  echo "SKIP_DB=1: not touching PostgreSQL. Put your DATABASE_URL in the .env yourself."
elif ! systemctl is-active --quiet postgresql 2>/dev/null; then
  if ss -ltn 2>/dev/null | grep -q ':5432 '; then
    echo "Something other than the system PostgreSQL service is using port 5432 (Docker?)." >&2
    echo "Re-run with SKIP_DB=1 and set DATABASE_URL in $BASE/shared/.env yourself." >&2
    exit 1
  fi
  apt-get install -y -qq postgresql > /dev/null
fi

echo "==> .env"
ENV_FILE="$BASE/shared/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  umask 077
  cat > "$ENV_FILE" <<EOF
DEBUG=False
SECRET_KEY=$(openssl rand -base64 60 | tr -d '\n=/+' | cut -c1-64)
ALLOWED_HOSTS=$DOMAIN,www.$DOMAIN
CSRF_TRUSTED_ORIGINS=https://$DOMAIN,https://www.$DOMAIN
SITE_URL=https://$DOMAIN
DATABASE_URL=
MEDIA_ROOT=$BASE/shared/media
STATIC_ROOT=$BASE/shared/static
DEFAULT_FROM_EMAIL=NarizMart <no-reply@$DOMAIN>
SUPPORT_EMAIL=hello@$DOMAIN
RESEND_API_KEY=PASTE_YOUR_RESEND_API_KEY_HERE
EOF
  chown "$APP_USER":"$APP_USER" "$ENV_FILE"; chmod 0600 "$ENV_FILE"   # only the app user (systemd reads it as root); Nginx must not
  echo "wrote $ENV_FILE  (edit it and add your RESEND_API_KEY; optional: REDIS_URL, WEB_CONCURRENCY)"
else
  echo "$ENV_FILE already exists, left unchanged"
fi

# Database role + database + DATABASE_URL (idempotent; see deploy/setup-database.sh)
if [[ "${SKIP_DB:-0}" != 1 ]]; then
  BASE="$BASE" bash "$HERE/setup-database.sh"
fi

echo "==> systemd units, sudoers, Nginx"
install -m 0644 "$HERE/narizmart.service" "$HERE/narizmart-purge.service" "$HERE/narizmart-purge.timer" \
  "$HERE/narizmart-backup.service" "$HERE/narizmart-backup.timer" /etc/systemd/system/
install -m 0440 "$HERE/narizmart.sudoers" /etc/sudoers.d/narizmart
visudo -cf /etc/sudoers.d/narizmart >/dev/null
# TLS certificate: a Cloudflare Origin Certificate if you saved one (recommended with Cloudflare), else Let's Encrypt paths.
CERT="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"
KEY="/etc/letsencrypt/live/$DOMAIN/privkey.pem"
if [[ -f /etc/ssl/cloudflare/origin.pem && -f /etc/ssl/cloudflare/origin.key ]]; then
  CERT=/etc/ssl/cloudflare/origin.pem
  KEY=/etc/ssl/cloudflare/origin.key
  chmod 600 "$KEY"
  echo "using the Cloudflare Origin Certificate in /etc/ssl/cloudflare"
else
  echo "no Cloudflare Origin Certificate found; Nginx will expect Let's Encrypt files (see the next steps below)"
fi
sed -e "s|__DOMAIN__|$DOMAIN|g" -e "s|__CERT__|$CERT|g" -e "s|__KEY__|$KEY|g" "$HERE/nginx.conf" > /etc/nginx/sites-available/narizmart
ln -sfn /etc/nginx/sites-available/narizmart /etc/nginx/sites-enabled/narizmart
# Never leave a broken site enabled: it would make every later Nginx reload fail for the OTHER apps on this server.
NGINX_OK=1
if ! nginx -t >/dev/null 2>&1; then
  NGINX_OK=0
  rm -f /etc/nginx/sites-enabled/narizmart
  echo "Nginx rejected the new site (usually the TLS certificate is missing), so it was NOT enabled. Details:" >&2
  nginx -t >&2 || true
fi
# Cloudflare: restore real visitor IPs (set CLOUDFLARE=0 to skip if you don't use Cloudflare)
if [[ "${CLOUDFLARE:-1}" == 1 ]]; then
  OUT=/etc/nginx/conf.d/cloudflare-realip.conf NGINX_RELOAD=0 bash "$HERE/cloudflare-realip.sh"
  install -m 0755 "$HERE/cloudflare-realip.sh" /usr/local/sbin/narizmart-cloudflare-realip
  printf '#!/bin/sh\n/usr/local/sbin/narizmart-cloudflare-realip >/dev/null 2>&1 || true\n' > /etc/cron.monthly/narizmart-cloudflare-ips
  chmod 0755 /etc/cron.monthly/narizmart-cloudflare-ips
fi
# older versions of this script used a systemd socket unit; remove it if it is still around
if [[ -f /etc/systemd/system/narizmart.socket ]]; then
  systemctl disable --now narizmart.socket >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/narizmart.socket
fi
usermod -aG systemd-journal "$APP_USER"   # lets the deploy show the service log if a release fails to start
systemctl daemon-reload
systemctl enable narizmart.service narizmart-purge.timer narizmart-backup.timer >/dev/null 2>&1
systemctl start narizmart-purge.timer narizmart-backup.timer   # the app itself starts with the first deploy
if [[ "$NGINX_OK" == 1 ]]; then systemctl reload nginx 2>/dev/null || systemctl start nginx; fi

if [[ "$CERT" == /etc/ssl/cloudflare/* ]]; then
  TLS_STEP=" 2. TLS is ready (Cloudflare Origin Certificate).
      (already enabled and reloaded if the config test passed)
    Then in Cloudflare set SSL/TLS mode to Full (strict)."
else
  TLS_STEP=" 2. Get a Let's Encrypt certificate (turn the orange cloud OFF for the DNS records while you do this), then start Nginx:
      sudo systemctl stop nginx
      sudo certbot certonly --standalone -d $DOMAIN -d www.$DOMAIN
      sudo nginx -t && sudo systemctl start nginx
    Then switch Cloudflare to Full (strict) and turn the orange cloud back on."
fi

cat <<EOF

Done. Remaining one-time steps:

 1. DNS: A records for $DOMAIN and www.$DOMAIN must point at this server.
$TLS_STEP
 3. Edit $ENV_FILE and set RESEND_API_KEY.
 4. Add the GitHub deploy public key to the app user:
      sudo -u $APP_USER mkdir -p -m 700 ~$APP_USER/.ssh
      echo 'PUBLIC_KEY_LINE' | sudo -u $APP_USER tee -a ~$APP_USER/.ssh/authorized_keys
      sudo chmod 600 ~$APP_USER/.ssh/authorized_keys
 5. Push to main: GitHub Actions tests, then deploys.
 6. After the first deploy, create your admin account:
      sudo -u $APP_USER bash -c 'cd $BASE/current && .venv/bin/python manage.py createsuperuser'

Firewall suggestion (run only after confirming you can still reach SSH):
      sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
EOF
