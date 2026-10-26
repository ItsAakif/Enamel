"""Development settings (default for manage.py)."""

from __future__ import annotations

import os
from pathlib import Path

# Safe local defaults for the compose stack only (deploy/docker-compose.yml
# publishes PostgreSQL on 5433 and Redis on 6380). Any other environment must
# set DATABASE_URL/REDIS_URL explicitly. Never production secrets.
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://enamel:enamel_dev_password@127.0.0.1:5433/enamel",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6380/0")

from .base import *  # noqa: E402,F403
from .base import env_list  # noqa: E402

DEBUG = True

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "127.0.0.1,localhost")
INTERNAL_IPS = ["127.0.0.1"]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Local development serves media directly from Django. Empty env -> backend/media.
_media_root = os.environ.get("MEDIA_ROOT", "").strip()
MEDIA_ROOT = Path(_media_root) if _media_root else BASE_DIR / "media"
