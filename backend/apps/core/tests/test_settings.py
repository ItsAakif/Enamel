"""Settings invariants: PostgreSQL-only database, AI keys, middleware order."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from config.settings.base import build_databases

AI_SETTING_KEYS = (
    "AI_PROVIDER",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "AI_CHAT_MODEL",
    "AI_EMBEDDING_MODEL",
    "AI_TIMEOUT_SECONDS",
    "AI_MAX_RETRIES",
    "AI_DAILY_BUDGET_USD",
    "AI_MOCK_MODE",
)


def test_sqlite_database_url_is_rejected():
    with pytest.raises(ImproperlyConfigured, match="PostgreSQL"):
        build_databases("sqlite:///db.sqlite3")


def test_postgres_database_url_is_accepted():
    databases = build_databases("postgresql://user:pass@db:5432/name")

    assert databases["default"]["ENGINE"] == "django.db.backends.postgresql"
    assert databases["default"]["OPTIONS"]["connect_timeout"] == 5


def test_ai_provider_settings_exist():
    for key in AI_SETTING_KEYS:
        assert hasattr(settings, key), f"missing AI setting: {key}"


def test_request_id_middleware_precedes_cors():
    middleware = settings.MIDDLEWARE

    request_id = middleware.index("apps.core.middleware.RequestIdMiddleware")
    security = middleware.index("django.middleware.security.SecurityMiddleware")
    cors = middleware.index("corsheaders.middleware.CorsMiddleware")
    session = middleware.index("django.contrib.sessions.middleware.SessionMiddleware")

    assert security < request_id < cors < session


def test_csrf_cookie_uses_django_default_name():
    assert settings.CSRF_COOKIE_NAME == "csrftoken"
