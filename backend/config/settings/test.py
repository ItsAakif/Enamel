"""Test settings for pytest and CI.

PostgreSQL and Redis are real dependencies (compose stack); tests never fall
back to SQLite. pytest-django creates ``test_enamel`` from ``DATABASE_URL``.
"""

from __future__ import annotations

import os
import tempfile

os.environ["DEBUG"] = "false"
# Compose-only defaults (deploy/docker-compose.yml publishes 5433/6380). CI or
# any other environment must provide DATABASE_URL/REDIS_URL explicitly.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://enamel:enamel_dev_password@127.0.0.1:5433/enamel",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6380/0")

from .base import *  # noqa: E402,F403

DEBUG = False

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Tests must never send mail over the network.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

# The portal runs as its own database role (ADR-013, decision D-3). The test
# suite forces the switch so every portal request is executed under the
# patient-scoped policies, exactly as it will be under the ``enamel_app``
# runtime in the shipped stack.
PORTAL_DB_ROLE_ENABLED = "1"

MEDIA_ROOT = tempfile.mkdtemp(prefix="enamel-test-media-")
STATIC_ROOT = tempfile.mkdtemp(prefix="enamel-test-static-")

# pytest-socket blocks outbound network calls globally (see addopts in
# pyproject.toml). Only loopback is allowed so PostgreSQL and Redis remain
# reachable; any external host raises SocketBlockedError/SocketConnectBlockedError.
