"""Scoped DRF throttles (ARCHITECTURE.md section 7).

Rates live in ``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]``. Throttle failures
are rendered by the central exception handler as a 429 ``throttled`` envelope;
DRF adds the ``Retry-After`` header from ``wait()``.
"""

from __future__ import annotations

import hashlib
from typing import Any

from rest_framework.throttling import BaseThrottle, SimpleRateThrottle


def _ip_ident(request) -> str:
    # DRF 3.18 exposes ``get_ident`` as a method on ``BaseThrottle`` rather
    # than a module-level helper. Keeping the proxy here means the X-Forwarded-For
    # / REMOTE_ADDR behaviour stays owned by DRF.
    return str(BaseThrottle().get_ident(request))


def _user_ident(request) -> str:
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return f"user:{user.pk}"
    return f"ip:{_ip_ident(request)}"


class AuthThrottle(SimpleRateThrottle):
    """5 attempts per minute per client IP (login, password reset)."""

    scope = "auth"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": _ip_ident(request)}


class UserRateThrottle(SimpleRateThrottle):
    """Base for authenticated per-user scoped throttles."""

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": _user_ident(request)}


class AiThrottle(UserRateThrottle):
    """30 AI requests per minute per user."""

    scope = "ai"


class ExportThrottle(UserRateThrottle):
    """5 exports per hour per user."""

    scope = "export"


def _phone_value(request) -> str | None:
    data = getattr(request, "data", None)
    if hasattr(data, "get"):
        for name in ("phone", "phone_e164", "phone_number"):
            value = data.get(name)
            if value:
                return str(value).strip()
    params = getattr(request, "query_params", None)
    if params is not None:
        value = params.get("phone")
        if value:
            return str(value).strip()
    return None


class _PublicBookingIpThrottle(SimpleRateThrottle):
    scope = "public_booking"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": _ip_ident(request)}


class _PublicBookingPhoneThrottle(SimpleRateThrottle):
    scope = "public_booking_phone"

    def get_cache_key(self, request, view):
        phone = _phone_value(request)
        if not phone:
            # Phone scope only applies when the request carries a phone number
            # (e.g. the first booking step).
            return None
        digest = hashlib.sha256(phone.encode("utf-8")).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class PublicBookingThrottle(BaseThrottle):
    """Composite throttle: 10/min/IP and 3/min/phone (phone when available)."""

    def __init__(self) -> None:
        self.throttles = (_PublicBookingIpThrottle(), _PublicBookingPhoneThrottle())
        self._wait: float | None = None

    def allow_request(self, request, view) -> bool:
        allowed = True
        waits: list[float] = []
        for throttle in self.throttles:
            if not throttle.allow_request(request, view):
                allowed = False
                wait = throttle.wait()
                if wait:
                    waits.append(float(wait))
        self._wait = max(waits) if waits else None
        return allowed

    def wait(self) -> float | None:
        return self._wait


def throttle_rate(scope: str) -> Any:
    """Small helper used by tests and docs to read the configured rate."""

    from django.conf import settings

    return settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {}).get(scope)
