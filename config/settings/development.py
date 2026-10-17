"""Local development: DEBUG on unless the environment says otherwise."""
import os

os.environ.setdefault("DEBUG", "True")

from .base import *  # noqa: E402,F401,F403
