"""Production security settings and header contract (T135).

The prod module is imported in-process after supplying the mandatory
environment values; importing it does not mutate the active test settings.
Header behaviour is then exercised with ``override_settings`` so the real
middleware stack (including django-csp) runs.
"""

from __future__ import annotations

import importlib
import os

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, override_settings

pytestmark = pytest.mark.django_db

#: A real (not development) Fernet key for the connector-credential cipher:
#: urlsafe base64 of 32 bytes.
TEST_CONNECTOR_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


@pytest.fixture
def prod(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "prod-test-secret-key-not-real")
    monkeypatch.setenv("ALLOWED_HOSTS", "app.enamel.test")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.enamel.test")
    monkeypatch.setenv("CSRF_TRUSTED_ORIGINS", "https://app.enamel.test")
    monkeypatch.setenv("PAYMENT_WEBHOOK_SECRET", "prod-test-payment-webhook-secret")
    monkeypatch.setenv("MESSAGING_WEBHOOK_SECRET", "prod-test-messaging-webhook-secret")
    monkeypatch.setenv("CONNECTOR_KEY", TEST_CONNECTOR_KEY)
    monkeypatch.delenv("DEBUG", raising=False)
    module = importlib.import_module("config.settings.prod")
    return importlib.reload(module)


def _reload_prod():
    """Re-execute the prod module, raising whatever it raises."""

    return importlib.reload(importlib.import_module("config.settings.prod"))


def test_prod_settings_fail_closed(prod):
    assert prod.DEBUG is False
    assert "app.enamel.test" in prod.ALLOWED_HOSTS
    assert prod.SECURE_SSL_REDIRECT is True
    assert prod.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
    assert prod.SECURE_HSTS_SECONDS >= 31536000
    assert prod.SECURE_HSTS_INCLUDE_SUBDOMAINS is True
    assert prod.SECURE_HSTS_PRELOAD is True
    assert prod.SECURE_CONTENT_TYPE_NOSNIFF is True
    assert prod.X_FRAME_OPTIONS == "DENY"
    assert prod.SECURE_REFERRER_POLICY == "same-origin"
    assert prod.SECURE_REDIRECT_EXEMPT


@pytest.mark.parametrize(
    "name",
    ["PAYMENT_WEBHOOK_SECRET", "MESSAGING_WEBHOOK_SECRET", "CONNECTOR_KEY"],
)
def test_prod_refuses_to_import_without_a_secret(prod, monkeypatch, name):
    """A missing secret must stop the process, not fall back to a default."""

    monkeypatch.delenv(name, raising=False)

    with pytest.raises(ImproperlyConfigured) as excinfo:
        _reload_prod()

    assert name in str(excinfo.value)


@pytest.mark.parametrize(
    ("name", "placeholder"),
    [
        ("PAYMENT_WEBHOOK_SECRET", "dev-payment-webhook-secret"),
        ("MESSAGING_WEBHOOK_SECRET", "dev-messaging-webhook-secret"),
    ],
)
def test_prod_refuses_the_development_webhook_placeholders(prod, monkeypatch, name, placeholder):
    """The well-known development literal must never reach production."""

    monkeypatch.setenv(name, placeholder)

    with pytest.raises(ImproperlyConfigured) as excinfo:
        _reload_prod()

    assert name in str(excinfo.value)


def test_prod_refuses_a_malformed_connector_key(prod, monkeypatch):
    monkeypatch.setenv("CONNECTOR_KEY", "not-a-fernet-key")

    with pytest.raises(ImproperlyConfigured) as excinfo:
        _reload_prod()

    assert "CONNECTOR_KEY" in str(excinfo.value)


@pytest.mark.parametrize(
    ("name", "placeholder"),
    [
        ("PAYMENT_WEBHOOK_SECRET", "dev-payment-webhook-secret"),
        ("MESSAGING_WEBHOOK_SECRET", "dev-messaging-webhook-secret"),
    ],
)
def test_prod_refuses_the_development_placeholder_with_surrounding_whitespace(
    prod, monkeypatch, name, placeholder
):
    """A trailing space must not smuggle the well-known literal through.

    The placeholder comparison runs on the stripped value, so
    ``"dev-payment-webhook-secret "`` is refused exactly like the literal.
    """

    monkeypatch.setenv(name, f"{placeholder} ")

    with pytest.raises(ImproperlyConfigured) as excinfo:
        _reload_prod()

    assert name in str(excinfo.value)


def test_prod_returns_the_stripped_secret(prod, monkeypatch):
    """The configured secret is stored without stray whitespace."""

    monkeypatch.setenv("PAYMENT_WEBHOOK_SECRET", "  prod-test-payment-webhook-secret  ")

    module = _reload_prod()

    assert module.PAYMENT_WEBHOOK_SECRET == "prod-test-payment-webhook-secret"


def test_prod_keeps_the_configured_secrets(prod):
    assert prod.PAYMENT_WEBHOOK_SECRET == "prod-test-payment-webhook-secret"
    assert prod.MESSAGING_WEBHOOK_SECRET == "prod-test-messaging-webhook-secret"
    assert prod.CONNECTOR_KEY == TEST_CONNECTOR_KEY


def test_prod_cookies_are_secure_with_a_documented_csrf_tradeoff(prod):
    assert prod.SESSION_COOKIE_HTTPONLY is True
    assert prod.SESSION_COOKIE_SECURE is True
    assert prod.SESSION_COOKIE_SAMESITE == "Lax"
    assert prod.CSRF_COOKIE_SECURE is True
    assert prod.CSRF_COOKIE_SAMESITE == "Lax"
    # The SPA reads the double-submit cookie; the session cookie stays HttpOnly.
    assert prod.CSRF_COOKIE_HTTPONLY is False


def test_prod_csp_has_no_inline_script_exception(prod):
    directives = prod.CONTENT_SECURITY_POLICY["DIRECTIVES"]

    assert directives["default-src"] == ["'self'"]
    assert directives["script-src"] == ["'self'"]
    assert directives["frame-ancestors"] == ["'none'"]
    assert directives["object-src"] == ["'none'"]
    assert "'unsafe-inline'" not in directives["script-src"]
    assert "'unsafe-eval'" not in directives["script-src"]
    assert "csp.middleware.CSPMiddleware" in prod.MIDDLEWARE


def _prod_middleware() -> list[str]:
    middleware = list(settings.MIDDLEWARE)
    if "csp.middleware.CSPMiddleware" not in middleware:
        middleware.append("csp.middleware.CSPMiddleware")
    return middleware


def _prod_headers_override(prod):
    return override_settings(
        DEBUG=False,
        MIDDLEWARE=_prod_middleware(),
        CONTENT_SECURITY_POLICY=prod.CONTENT_SECURITY_POLICY,
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        SECURE_SSL_REDIRECT=True,
        SECURE_REDIRECT_EXEMPT=[r"^healthz$", r"^readyz$"],
        SECURE_HSTS_SECONDS=31536000,
        SECURE_HSTS_INCLUDE_SUBDOMAINS=True,
        SECURE_HSTS_PRELOAD=True,
        SECURE_CONTENT_TYPE_NOSNIFF=True,
        SECURE_REFERRER_POLICY="same-origin",
        X_FRAME_OPTIONS="DENY",
    )


def test_security_headers_on_a_404_are_strict(prod):
    with _prod_headers_override(prod):
        response = Client().get("/definitely-not-a-route", HTTP_X_FORWARDED_PROTO="https")

    assert response.status_code == 404
    assert response["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains; preload"
    )
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["X-Frame-Options"] == "DENY"
    assert response["Referrer-Policy"] == "same-origin"

    csp = response["Content-Security-Policy"]
    directives = {
        part.split(" ", 1)[0]: part.split(" ", 1)[1]
        for part in (piece.strip() for piece in csp.split(";"))
        if " " in part
    }
    assert directives["default-src"] == "'self'"
    assert directives["script-src"] == "'self'"
    assert directives["frame-ancestors"] == "'none'"

    body = response.content.decode()
    assert "Traceback" not in body
    assert "SECRET_KEY" not in body


def test_unhandled_error_does_not_leak_debug_details(prod):
    with _prod_headers_override(prod), override_settings(
        ROOT_URLCONF="apps.core.tests.urls_error"
    ):
        client = Client(raise_request_exception=False)
        response = client.get("/boom/", HTTP_X_FORWARDED_PROTO="https")

    assert response.status_code == 500
    body = response.content.decode()
    assert "secret-token-do-not-leak" not in body
    assert "Traceback" not in body
    assert "RuntimeError" not in body


def test_prod_refuses_demo_mode(prod, monkeypatch):
    """DEMO_MODE reveals the portal one-time code; production never runs it.

    The check reads the environment like the DEBUG check, so the flag is
    refused whether it arrives as ``true``, ``1`` or ``yes``.
    """

    for value in ("true", "1", "yes", "on"):
        monkeypatch.setenv("DEMO_MODE", value)
        with pytest.raises(ImproperlyConfigured) as excinfo:
            _reload_prod()
        assert "DEMO_MODE" in str(excinfo.value)


def test_prod_refuses_demo_mode_even_uppercase(prod, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "TRUE")
    with pytest.raises(ImproperlyConfigured) as excinfo:
        _reload_prod()
    assert "DEMO_MODE" in str(excinfo.value)


def test_prod_defaults_to_demo_mode_disabled(prod):
    """Without the env var the non-superdefault is off."""

    assert prod.DEMO_MODE is False


def test_demo_settings_allows_demo_mode(monkeypatch):
    """The dedicated demo profile is the only place DEMO_MODE may be on.

    ``config.settings.demo`` hides the flag from the production import (which
    would refuse it), then states it explicitly. Everything else is the
    hardened production posture.
    """

    # The module body writes back to the process environment on purpose
    # (companion of the DEL that hides the flag from the prod import); restore
    # it explicitly instead of relying on monkeypatch, which only recorded
    # mutations through its own calls.
    saved_demo_mode = os.environ.pop("DEMO_MODE", None)
    monkeypatch.setenv("SECRET_KEY", "prod-test-secret-key-not-real")
    monkeypatch.setenv("ALLOWED_HOSTS", "demo.enamel.test")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://demo.enamel.test")
    monkeypatch.setenv("CSRF_TRUSTED_ORIGINS", "https://demo.enamel.test")
    monkeypatch.setenv("PAYMENT_WEBHOOK_SECRET", "demo-test-payment-webhook-secret")
    monkeypatch.setenv("MESSAGING_WEBHOOK_SECRET", "demo-test-messaging-webhook-secret")
    monkeypatch.setenv("CONNECTOR_KEY", TEST_CONNECTOR_KEY)

    try:
        module = importlib.import_module("config.settings.demo")
        module = importlib.reload(module)

        assert module.DEMO_MODE is True
        assert module.DEBUG is False
        assert module.SECURE_HSTS_SECONDS >= 31536000
        assert module.SESSION_COOKIE_SECURE is True
        assert getattr(module, "DEMO_OTP_NOTICE", "")
    finally:
        if saved_demo_mode is None:
            os.environ.pop("DEMO_MODE", None)
        else:
            os.environ["DEMO_MODE"] = saved_demo_mode


def test_healthz_is_exempt_from_the_https_redirect(prod):
    with _prod_headers_override(prod):
        response = Client().get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
