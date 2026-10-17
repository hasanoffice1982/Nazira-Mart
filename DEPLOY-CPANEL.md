# Deploy NarizMart on cPanel (Python App + MySQL)

Server: moon.serverdns.biz, account `salespal`. Database: `salespal_narizmart`.

1. **Domain**: cPanel > Domains: add `narizmart.com` as an addon domain (document root e.g. `/home/salespal/narizmart.com`). Point the domain's DNS/nameservers at the server; SSL issues via AutoSSL.
2. **Code**: cPanel > Git Version Control > Create: clone your repo to `/home/salespal/narizmart` (or upload the folder with File Manager). Update later with *Update from Remote*.
3. **Python App**: cPanel > Setup Python App > Create Application: Python 3.10+ (3.11/3.12 if offered), app root `narizmart_app` (created by the deploy step below), URL `narizmart.com`, startup file `passenger_wsgi.py`, entry point `application`.
4. **Settings**: copy `.env.cpanel.example` to `/home/salespal/narizmart/.env` and fill in SECRET_KEY and the DB user/password.
5. **Install**: in the Python App page copy the "Enter to the virtual environment" command, run it in cPanel > Terminal (or SSH), then:
   ```
   pip install -r requirements-cpanel.txt
   export DJANGO_SETTINGS_MODULE=config.settings.production
   python manage.py migrate
   python manage.py collectstatic --noinput
   python manage.py seed_catalog
   python manage.py createsuperuser
   ```
6. **Static files**: WhiteNoise serves `/static/`. User uploads (`media/`) are not served by Django in production; add an Apache alias for `/media/` to `MEDIA_ROOT` or use a `.htaccess` rule.
7. Click **Restart** in Setup Python App. Admin lives at the `ADMIN_URL` path.

Notes: MySQL is not what the original project's CI tests against (PostgreSQL); run through checkout and admin once after deploy.

## Security checklist (shared cPanel hosting)

- **Database**: dedicated MySQL user for this one database only. Never use the cPanel login for the app. *Remote Database Access* stays empty (MySQL reachable from localhost only). Use a long random password.
- **Secrets**: `.env` lives in the app folder outside `public_html`; set `chmod 600 .env`. Never commit it (already in `.gitignore`).
- **Django**: production settings are already strict (DEBUG off, HTTPS redirect, HSTS, CSP, login lockout via django-axes, rate limits). Set `ADMIN_URL` to a secret path and turn on the built-in two-factor login for staff.
- **SSH / Git**: use key-only SSH (cPanel > SSH Access > Manage Keys), no password logins. Use a read-only deploy key for the private repo.
- **phpMyAdmin / PHP**: no PHP code is used by this app; do not install WordPress or other PHP apps in the same account. Enable ModSecurity and cPGuard (both listed in this cPanel) and AutoSSL.
- **Backups**: enable JetBackup daily, plus a nightly `mysqldump` cron into a folder outside the web root.
- **Cloudflare** (optional, recommended): proxy narizmart.com for DDoS/WAF protection.
- **Updates**: re-run `pip install -r requirements-cpanel.txt` on each release; pins are in the file.

## Keeping the server small
`.cpanel.yml` copies only `apps/ config/ templates/ static/ manage.py passenger_wsgi.py requirements-cpanel.txt` to `/home/salespal/narizmart_app` on each deploy (Git Version Control > Manage > Deploy HEAD Commit). Use `/home/salespal/narizmart_app` as the Python App root and keep `.env` there. Tests, docs, sample data and CI files stay in Git only. Run `pytest` locally before pushing.
