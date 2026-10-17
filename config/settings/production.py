"""Production: DEBUG off. All hardening lives in base.py behind `if PRODUCTION`."""
import os

os.environ["DEBUG"] = "False"

from .base import *  # noqa: E402,F401,F403
