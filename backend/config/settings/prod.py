"""Production settings.

Fail fast on missing secrets/configuration. Served behind nginx; TLS is
terminated upstream (proxy header accepted). No development default is trusted
here: ``SECRET_KEY`` and ``ALLOWED_HOSTS`` must be supplied explicitly, and so
must the two webhook secrets and the connector key.

Reverse-proxy contract: nginx (or a cloud load balancer) terminates TLS and
forwards ``X-Forwarded-Proto: https``. The container's own healthcheck talks
HTTP over loopback; ``/healthz`` and ``/readyz`` are exempt from the HTTPS
redirect so the probe does not chase a TLS handshake inside the container.
"""

from __future__ import annotations

import base64
import binascii
import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: E402,F403
from .base import (  # noqa: E402
    DEV_MESSAGING_WEBHOOK_SECRET,
    DEV_PAYMENT_WEBHOOK_SECRET,
    env_bool,
    env_list,
)

DEBUG = False

if not os.environ.get("SECRET_KEY"):
    raise ImproperlyConfigured("SECRET_KEY is required when DEBUG=false (production).")
if os.environ.get("DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}:
    raise ImproperlyConfigured("DEBUG must be false in production.")
# DEMO_MODE reveals the portal one-time code in the request-code response. That
# is only acceptable for a dedicated demo deployment of synthetic data, which
# is served by a separate compose overlay and must never be the production
# profile. (The check reads the environment like the DEBUG check, so a reload
# with the value set is refused too.)
if os.environ.get("DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "on"}:
    raise ImproperlyConfigured(
        "DEMO_MODE must be false in production: the setting reveals the portal "
        "one-time code in the API response and must never run on a deployment "
        "that handles real patient data."
    )

# ALLOWED_HOSTS must be supplied explicitly; never inherit the localhost list.
if not os.environ.get("ALLOWED_HOSTS", "").strip():
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS must be set explicitly in production (comma-separated hostnames)."
    )
# Read the environment here rather than inheriting the value base evaluated at
# its first import (settings reloads and tests must see the current value).
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")


# --- Secrets that must never keep a development default ---------------------


def _require_secret(name: str, development_default: str) -> str:
    """Return the configured secret, or refuse to start the process.

    A missing value *and* the well-known development literal are both fatal:
    the webhook HMAC and the connector key are the only thing standing between
    an anonymous caller and a forged payment or delivery callback.

    The value is compared and returned **stripped**: a secret that reaches the
    process as ``"dev-payment-webhook-secret "`` (a trailing space from a
    ``.env`` file or a shell) is still the well-known literal and must be
    refused, and the stored secret must not carry stray whitespace.
    """

    value = os.environ.get(name, "")
    stripped = value.strip()
    if not stripped:
        raise ImproperlyConfigured(
            f"{name} is required in production. Generate one with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    if stripped == development_default:
        raise ImproperlyConfigured(
            f"{name} is still the development placeholder. "
            "Set a real, random secret before starting in production."
        )
    return stripped


PAYMENT_WEBHOOK_SECRET = _require_secret(
    "PAYMENT_WEBHOOK_SECRET", DEV_PAYMENT_WEBHOOK_SECRET
)
MESSAGING_WEBHOOK_SECRET = _require_secret(
    "MESSAGING_WEBHOOK_SECRET", DEV_MESSAGING_WEBHOOK_SECRET
)
CONNECTOR_KEY = _require_secret("CONNECTOR_KEY", "")

# The connector key encrypts stored PMS credentials. It has to be a Fernet
# key (urlsafe base64 of 32 bytes); anything else would fail later, at the
# first write, with a key error that names the setting.
try:
    _connector_key_bytes = base64.urlsafe_b64decode(CONNECTOR_KEY.encode("utf-8"))
except (ValueError, binascii.Error) as exc:
    raise ImproperlyConfigured(
        "CONNECTOR_KEY must be a urlsafe-base64 32-byte Fernet key."
    ) from exc
if len(_connector_key_bytes) != 32:
    raise ImproperlyConfigured(
        "CONNECTOR_KEY must be a urlsafe-base64 32-byte Fernet key."
    )

# The image healthcheck probes over loopback (see deploy/backend.Dockerfile).
# Add only the loopback hostnames so a strict operator-supplied list still lets
# the container report healthy.
for _loopback_host in ("127.0.0.1", "localhost"):
    if _loopback_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_loopback_host)

# Browser origins must be configured explicitly; local development origins are
# never trusted by default. These are the SPA's public origins.
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")

# --- Transport and headers -------------------------------------------------

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
# The liveness/readiness probes answer on plain HTTP inside the container.
SECURE_REDIRECT_EXEMPT = [r"^healthz$", r"^readyz$"]

# One year, subdomains and preload are mandatory in production.
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
if SECURE_HSTS_SECONDS < 31536000:
    raise ImproperlyConfigured(
        "SECURE_HSTS_SECONDS must be at least 31536000 (one year) in production."
    )
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

X_FRAME_OPTIONS = "DENY"

# --- Cookies ---------------------------------------------------------------

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"

# Deliberate trade-off: the React SPA performs the double-submit CSRF pattern,
# so JavaScript must be able to read the CSRF cookie and echo it in
# ``X-CSRFToken``. The session cookie stays HttpOnly. If the SPA is ever changed
# to fetch the token from the DOM/JSON endpoint instead, set this to True.
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = "Lax"

# --- Content Security Policy ----------------------------------------------

# Strict by default: scripts come only from the built same-origin bundle. The
# SPA's HTML preconnects to Google Fonts, so the two exact font origins are
# allowlisted. ``'unsafe-inline'`` is limited to styles (Tailwind/runtime style
# attributes); scripts never receive it.
#
# Django admin uses inline event handlers. It is excluded from this header
# rather than weakening ``script-src`` for the API and app. Admin is an
# operator-only surface; keep it behind network/VPN access control.
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ["'self'"],
        "base-uri": ["'self'"],
        "form-action": ["'self'"],
        "frame-ancestors": ["'none'"],
        "object-src": ["'none'"],
        "script-src": ["'self'"],
        "style-src": ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
        "img-src": ["'self'", "data:", "blob:"],
        "font-src": ["'self'", "data:", "https://fonts.gstatic.com"],
        "connect-src": ["'self'"],
        "media-src": ["'self'", "blob:"],
        "worker-src": ["'self'", "blob:"],
        "manifest-src": ["'self'"],
    },
    "EXCLUDE_URL_PREFIXES": ["/admin/"],
}

# Hashed + compressed static files with a manifest (collectstatic runs in the image).
# The default media backend stays local-disk so the local Compose stack and the
# public tests are unchanged. When an object store is configured (AWS_STORAGE_
# BUCKET_NAME, e.g. a private Supabase Storage bucket exposed over its S3
# endpoint), the default (media) backend becomes S3 with signed download URLs;
# staticfiles always stays on whitenoise.
def _s3_bucket_configured() -> bool:
    return bool(os.environ.get("AWS_STORAGE_BUCKET_NAME", "").strip())


def _media_storage_backend() -> str:
    """Pick the default (media) storage backend.

    Unset bucket -> the local ``FileSystemStorage`` default is left untouched.
    A configured bucket with an incomplete S3 configuration (missing keys or
    endpoint) -> refuse to boot: a half-configured object store would silently
    keep writing media to the ephemeral container disk.
    """

    if not _s3_bucket_configured():
        return "django.core.files.storage.FileSystemStorage"

    missing = [
        name
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_S3_ENDPOINT_URL")
        if not os.environ.get(name, "").strip()
    ]
    if missing:
        raise ImproperlyConfigured(
            "AWS_STORAGE_BUCKET_NAME is set but the S3 configuration is "
            f"incomplete (missing: {', '.join(missing)}). Set AWS_ACCESS_KEY_ID, "
            "AWS_SECRET_ACCESS_KEY and AWS_S3_ENDPOINT_URL, or unset "
            "AWS_STORAGE_BUCKET_NAME to keep media on the local disk."
        )
    return "storages.backends.s3.S3Storage"


_MEDIA_STORAGE_BACKEND = _media_storage_backend()  # fail closed at import

if _MEDIA_STORAGE_BACKEND == "storages.backends.s3.S3Storage":
    # django-storages reads these from Django settings (not directly from the
    # environment), so pass the standard env names through. Signed, private
    # URLs (AWS_QUERYSTRING_AUTH + the s3v4 signature) keep objects
    # unreachable without a Django-issued link; path-style addressing is what
    # Supabase Storage's S3 endpoint expects; no default ACL and no
    # overwrite-on-same-name keep content-addressed media stable and
    # non-public. AWS_LOCATION keeps objects under the same ``media`` path
    # convention as the local backend.
    AWS_STORAGE_BUCKET_NAME = os.environ["AWS_STORAGE_BUCKET_NAME"].strip()
    AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"].strip()
    AWS_SECRET_ACCESS_KEY = os.environ["AWS_SECRET_ACCESS_KEY"].strip()
    AWS_S3_ENDPOINT_URL = os.environ["AWS_S3_ENDPOINT_URL"].strip()
    _region = os.environ.get("AWS_S3_REGION_NAME", "").strip()
    if _region:
        AWS_S3_REGION_NAME = _region
    AWS_LOCATION = "media"
    AWS_QUERYSTRING_AUTH = True
    AWS_S3_ADDRESSING_STYLE = "path"
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None
    AWS_S3_SIGNATURE_VERSION = "s3v4"


STORAGES = {
    "default": {"BACKEND": _MEDIA_STORAGE_BACKEND},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MIDDLEWARE = [*MIDDLEWARE, "csp.middleware.CSPMiddleware"]
