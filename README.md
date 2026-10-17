# NarizMart

Django 5.2 + HTMX storefront (PWA). Design source: `../reference/NarizMart_UI_Demo.html`.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest pytest-django pillow
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_catalog
.venv/bin/python manage.py createsuperuser      # staff login at /admin/ (set ADMIN_URL in .env to move it)
.venv/bin/python manage.py runserver
.venv/bin/python -m pytest
```

MVP: catalog, search/filter, guest→user cart merge, wishlist, email login, COD checkout, order history + status timeline, admin order management, installable PWA.
Roadmap: `../roadmap.md`.

## Development
```bash
make test                # pytest (SQLite). Set DATABASE_URL=postgres://... to test on PostgreSQL like CI does
ruff check .
```
CI/CD and server setup: see [DEPLOY.md](DEPLOY.md).
