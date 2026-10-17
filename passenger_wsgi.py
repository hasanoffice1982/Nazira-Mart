"""Entry point for cPanel "Setup Python App" (Passenger)."""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
from config.wsgi import application  # noqa: E402,F401
