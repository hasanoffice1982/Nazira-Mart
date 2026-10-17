# NarizMart — deployment guide

How code gets to production: **push to `main` → GitHub Actions runs CI → if green, the Deploy workflow ships it to your server over SSH.**
No Docker: it uses Python virtualenvs, systemd, Gunicorn and Nginx, which is light on a small VPS (this guide is sized for ~2 vCPU / 4 GB RAM).

```
GitHub push ──► CI (lint · tests on PostgreSQL · checks)  ──► Deploy
                                                              │ ssh: upload release, build venv, collectstatic,
                                                              │ pg_dump backup, migrate, switch symlink, restart,
                                                              ▼ health check ── fails? automatic rollback
                               /srv/narizmart/current ─► gunicorn (unix socket) ─► nginx ─► users
```

## 1. Cloudflare + server setup (Ubuntu 22.04 / 24.04) — domain: narizmart.com

### 1a. Cloudflare dashboard (narizmart.com → already on Cloudflare nameservers)
1. **DNS → Records → Add record** (set *Proxy status* to **Proxied**, orange cloud):

   | Type | Name | Content |
   |---|---|---|
   | A | `@` | your VPS IP address |
   | A (or CNAME → `narizmart.com`) | `www` | your VPS IP address |

   SSH is **not** proxied by Cloudflare. GitHub connects to the server by IP address (`SSH_HOST` = the IP), never by the domain.
2. **SSL/TLS → Origin Server → Create Certificate**: RSA, hostnames `narizmart.com` and `*.narizmart.com`, validity 15 years. Copy the **certificate** and the **private key** (the key is shown once).
3. **SSL/TLS → Overview**: set the mode to **Full (strict)** (after step 1b below, otherwise visitors see error 526).
4. **SSL/TLS → Edge Certificates**: *Always Use HTTPS* on, *Minimum TLS* 1.2, *Automatic HTTPS Rewrites* on.
5. **Speed → Optimization**: turn **Rocket Loader OFF** (it injects inline scripts that the site's Content-Security-Policy blocks) and turn any *Auto Minify* off.
6. **Scrape Shield**: turn **Email Address Obfuscation OFF** (same reason).
7. **Caching → Configuration**: *Browser Cache TTL* = **Respect Existing Headers**. Optional: a *Cache Rule* that bypasses the cache for the path `/sw.js`.

### 1b. Server

**Sharing the server with other apps?** First run the read-only check (it changes nothing and prints no secrets) and look at the warnings:
```bash
sudo bash /root/narizmart/deploy/preflight.sh
```
The setup script is built for shared servers: it never removes other apps' Nginx sites, only *reloads* Nginx (after `nginx -t` passes), never touches existing databases, and defaults to 2 Gunicorn workers with a 1 GB memory cap so it fits beside other apps. If PostgreSQL runs in Docker or on another machine, run the setup with `SKIP_DB=1` and set `DATABASE_URL` in the `.env` yourself.
1. Get the `deploy/` folder onto the server **without copying anything by hand**: after your first `git push`, clone the repository there.
   ```bash
   git clone https://github.com/hasanoffice1982/narizmart.git /root/narizmart
   ```
   (Private repository? Use a read-only fine-grained access token as the password, or make the repository public: it contains no secrets.) Later releases are delivered by the Deploy workflow; this clone is only used for the one-time setup.
2. On the server, save the Cloudflare Origin Certificate files **before** running the setup:
   ```bash
   mkdir -p /etc/ssl/cloudflare
   nano /etc/ssl/cloudflare/origin.pem     # paste the certificate
   nano /etc/ssl/cloudflare/origin.key     # paste the private key
   chmod 600 /etc/ssl/cloudflare/origin.key
   ```
3. Run the setup:
   ```bash
   bash /root/narizmart/deploy/bootstrap-server.sh narizmart.com
   ```
   It installs Nginx/PostgreSQL/Python, creates the `narizmart` user, directories, database, systemd units, the Nginx site (using the Origin Certificate), a 2 GB swap file and a first `/srv/narizmart/shared/.env`. It also installs the Cloudflare **real-IP** config (refreshed monthly) so rate limits and login lockouts see each visitor's own address instead of Cloudflare's. Safe to re-run. Then start Nginx: `nginx -t && systemctl restart nginx`.
4. Edit `/srv/narizmart/shared/.env` and set `RESEND_API_KEY` (see §4). Keep one `KEY=value` per line, no inline comments.
5. Authorize the GitHub deploy key (the script prints the exact commands).

Not using Cloudflare's certificate? Run the script with `CLOUDFLARE=0`, turn the orange cloud off while issuing a Let's Encrypt certificate with `certbot certonly --standalone`, and the script's final message explains the rest.

## 2. GitHub setup (one time)
Repository → **Settings → Environments → New environment → `production`** (tick *Required reviewers* if you want an approval click before every deploy).

| Kind | Name | Value |
|---|---|---|
| Secret | `SSH_HOST` | the server's **IP address** (not the proxied domain) |
| Secret | `SSH_USER` | `narizmart` |
| Secret | `SSH_PRIVATE_KEY` | contents of the private deploy key |
| Secret | `SSH_KNOWN_HOSTS` | output of `ssh-keyscan -p 22 your.server` run **from your own machine** and checked against the server's real fingerprint |
| Variable | `APP_DOMAIN` | `narizmart.com` |
| Variable | `SSH_PORT` | only if not 22 |

Also: **Settings → Branches → add a rule for `main`** requiring the *CI* checks to pass before merging.

Finally switch deployments on: **Settings → Secrets and variables → Actions → Variables → New repository variable** `DEPLOY_ENABLED` = `true`. Until then pushes only run CI.

Push your code:
```bash
git push -u origin main
```
The first push runs CI, then Deploy. Watch it in the **Actions** tab.

## 3. What the workflows do
- **CI** (`.github/workflows/ci.yml`) on every push and pull request: Ruff lint, JavaScript and shell-script syntax, then the full test suite **on PostgreSQL 16** with coverage (fails under 88%), migrations-in-sync check, `check --deploy` against production settings and a production `collectstatic`. A weekly-style dependency audit runs too but never blocks a deploy.
- **Deploy** (`.github/workflows/deploy.yml`) only after CI succeeded on a push to `main` (or manually: Actions → Deploy → *Run workflow*, e.g. to redeploy an older commit). One deploy at a time.
- **On the server** `deploy/deploy.sh` does: reuse or build the virtualenv → Django checks → `collectstatic` → **`pg_dump` backup** (last 10 kept in `shared/backups`) → `migrate` → switch `current` to the new release → restart → wait for `/healthz` → delete old releases (3 kept).
- If anything fails **before** the switch, the live site is never touched. If it fails **after** (the new version does not become healthy within 60 s) the previous release is restored automatically.

**Database migrations are not undone by a rollback.** Keep them backward compatible: add columns/tables in one release and remove old ones in a later one. A pre-deploy backup is always available for emergencies:
```bash
gunzip -c /srv/narizmart/shared/backups/pre-<sha>-<time>.sql.gz | psql "$DATABASE_URL"
```

Manual rollback (to the most recent successfully deployed release, or name one):
```bash
sudo -u narizmart /srv/narizmart/current/deploy/rollback.sh
```

## 3b. Optional: a separate SSH port for this app
SSH port 22 is shared by everything on a server, so on a server with other apps do not close or move it. `deploy/add-ssh-port.sh` adds a **second** SSH port that only the accounts you list may use; port 22 is unchanged for everyone else, with the firewall and fail2ban updated and an `--undo` option:
```bash
sudo bash deploy/add-ssh-port.sh 47531 narizmart hasan
```
Then point this app's pipeline at it: set the `SSH_PORT` variable to `47531`, and use the **bracketed** host-key line in the `SSH_KNOWN_HOSTS` secret (SSH looks keys up by host *and* port): `[103.191.50.74]:47531 ssh-ed25519 AAAA...` (the script prints the exact line). Also open the port in your hosting provider's own firewall if it has one. Once every other app has moved too, you may close 22 for good.

## 3c. Versions and releases
**Every push to `main` that passes CI goes live.** What identifies a release:
- **Commit id**: the exact code, e.g. `6476069…`. It is shown as `version` at `/healthz`.
- **Version label**: a human-readable name from the nearest tag, shown as `release` at `/healthz` and in the admin footer: `v1.2.0` when the commit is the tag itself, `v1.2.0-3-g6476069` three commits after it, or the short commit id before any tag exists.

**Naming a release (semantic versions `vMAJOR.MINOR.PATCH`):** bump MAJOR for breaking changes, MINOR for new features, PATCH for fixes (`v1.4.2`). Add a suffix for a trial build: `v1.5.0-rc.1` (published as a pre-release).
```bash
git checkout main && git pull
git tag -a v1.0.0 -m "First public release"
git push origin v1.0.0
```
Pushing the tag runs the **Release** workflow (to publish a tag that already exists, use **Actions → Release → Run workflow** and type the tag), which checks that the tag is a valid version, that the commit is on `main`, and that CI passed for that exact commit, then publishes a **GitHub Release** with automatic notes (pull requests grouped by label: `feature`, `bug`, `security`, `dependencies`; add `skip-changelog` to leave one out). The tag does not itself deploy: production already runs that commit.

**Going back:**
- Last release: `sudo -u narizmart /srv/narizmart/current/deploy/rollback.sh`
- By version: `... rollback.sh v1.0.0` (or a commit id). The server keeps the 3 newest releases; list them with `cat /srv/narizmart/shared/deploy-history`.
- Redeploy any older tag or commit from GitHub (works even after it was cleaned from the server): **Actions → Deploy → Run workflow**, ref `v1.0.0`.
- Rolling back never undoes database migrations (see §3).

**Hotfix:** branch from `main`, fix, open a pull request, merge once CI is green (it deploys automatically), then tag `v1.0.1`.

## 4. Email (Resend)
1. Resend dashboard → **Domains → Add Domain**, create the DNS records it lists (SPF + DKIM) and wait for **Verified**.
2. **API Keys → Create** (sending access, limited to your domain). Put it in `.env` as `RESEND_API_KEY=re_...`; that single setting configures SMTP.
3. Set `DEFAULT_FROM_EMAIL=NarizMart <no-reply@narizmart.com>` (must be on the verified domain) and `SUPPORT_EMAIL` (where the contact form delivers).
4. Test it: `sudo -u narizmart bash -c 'cd /srv/narizmart/current && .venv/bin/python manage.py send_test_email you@yourdomain.com'`, then request a real sign-in code and place a test order.

## 5. Production settings reference (`/srv/narizmart/shared/.env`)
| Variable | Notes |
|---|---|
| `DEBUG=False`, `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | required; the app refuses to start without the first three |
| `DATABASE_URL` | `postgres://user:pass@localhost:5432/narizmart` (written by the bootstrap) |
| `ADMIN_URL` | where the admin lives, e.g. `manage-k7x2p9/` (default `admin/`). The old `/admin/` then returns 404. Bookmark the new address; the emailed sign-in code and lockouts remain the real protection |
| `SITE_URL` | `https://narizmart.com`, used for links in emails |
| `IMPORT_TMP_DIR` | where an uploaded photo ZIP waits during a CSV import (default: `import_tmp` next to `MEDIA_ROOT`, never served by Nginx) |
| `MEDIA_ROOT`, `STATIC_ROOT` | shared folders that survive deploys (written by the bootstrap) |
| `RESEND_API_KEY`, `DEFAULT_FROM_EMAIL`, `SUPPORT_EMAIL` | email, see §4 |
| `REDIS_URL` | optional: shares rate-limit counters between workers (`apt install redis-server`, `redis://localhost:6379/1`) |
| `WEB_CONCURRENCY` | Gunicorn workers, default 3 (about 150 MB each) |

`APP_VERSION` is written by each deploy; it changes the service-worker version so browsers update.

## 6. Database: automated setup and nightly backups
- **Setup is automatic.** `bootstrap-server.sh` runs `deploy/setup-database.sh`, which creates the `narizmart` role and database on your PostgreSQL, generates a random password and writes `DATABASE_URL` into `/srv/narizmart/shared/.env`. It is safe to re-run (nothing existing is changed). It revokes public access to the new database, so the other apps' database users cannot read it, and it never touches any other database. To run it on its own: `sudo bash deploy/setup-database.sh`. If you ever lose the URL, `RESET_PASSWORD=1 sudo bash deploy/setup-database.sh` sets a new password.
- **Migrations are automatic**: every deploy runs `migrate` after taking a backup (§3).
- **Nightly backups are automatic**: `narizmart-backup.timer` runs `deploy/backup-db.sh` at about 02:30, keeps the 14 newest dumps in `/srv/narizmart/shared/backups` (mode 600) and refuses to keep a dump that fails its integrity check. Check it with `systemctl list-timers | grep narizmart` and `sudo systemctl start narizmart-backup.service`.
- **Restore** into an empty database: `gunzip -c /srv/narizmart/shared/backups/nightly-<time>.sql.gz | psql "$DATABASE_URL"`.
- **Off-server copies are still up to you.** The dumps sit on the same disk as the database; copy them somewhere else (another server, or object storage with `rclone`) before you rely on them.

## 6b. Daily housekeeping
A systemd timer (`narizmart-purge.timer`, installed by the bootstrap) runs `purge_checkout_otps` every day at 03:15 to delete expired codes. Check it with `systemctl list-timers | grep narizmart`.

## 7. First launch checklist
1. Create your admin account: `sudo -u narizmart bash -c 'cd /srv/narizmart/current && .venv/bin/python manage.py createsuperuser'` (optional demo data: `manage.py seed_catalog`).
2. **Read and edit the starter pages** in Admin → Pages (privacy policy, terms, returns, shipping, help). They are drafts, including the placeholder delivery times and return window shown in `[brackets]`; have the legal text reviewed for Bangladesh before you take real orders.
3. Smoke test: home page, guest checkout with the emailed code, admin sign-in with its emailed code, password reset email.
4. Install the PWA on a phone (Chrome → *Add to Home screen*) and confirm it opens offline.
5. Set up off-server backups of `/srv/narizmart/shared/backups` and `/srv/narizmart/shared/media` (the pre-deploy dumps live on the same disk as the database).

## Useful commands
```bash
sudo journalctl -u narizmart -f                 # application logs
sudo systemctl status narizmart.service
sudo systemctl restart narizmart.service
curl -s https://narizmart.com/healthz              # {"status": "ok", "version": "<sha>"}
make deploy-check                                # production settings audit (with DEBUG=False env)
```

## Upload limits
- A single photo: up to 5 MB, compressed to about 150 KB (1600 px WebP) when saved.
- Bulk photo upload on a product: up to 20 photos and 100 MB per upload.
- CSV import photo ZIP: up to 500 MB. The import page sends it in 20 MB pieces in the background (Cloudflare's free plan rejects any single request above 100 MB, Nginx allows 110 MB), joins them on the server and shows progress; the "Upload & preview" button stays disabled until the upload has finished. Pieces wait in `IMPORT_TMP_DIR` (default `shared/import_tmp`, never served by Nginx) and unfinished ones are removed after 24 hours.
