PY ?= .venv/bin/python

.PHONY: run test migrate static check deploy-check purge seed

run:            ## local dev server
	$(PY) manage.py runserver

test:
	$(PY) -m pytest -q

migrate:        ## run on EVERY database (dev, staging, production) after pulling new code
	$(PY) manage.py migrate --noinput

static:         ## required when DEBUG=False (fingerprinted static files)
	$(PY) manage.py collectstatic --noinput

check:
	$(PY) manage.py check

deploy-check:   ## production settings audit (run with DEBUG=False env loaded)
	$(PY) manage.py check --deploy

purge:          ## delete expired checkout + sign-in codes (cron runs this daily)
	$(PY) manage.py purge_checkout_otps

seed:
	$(PY) manage.py seed_catalog
