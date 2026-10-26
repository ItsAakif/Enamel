"""Public booking throttles (T112).

The booking create endpoint is unauthenticated, so it is rate limited per client
IP (10/min) and per submitted phone number (3/min). The phone is nested under
``patient.phone`` in the request body, which the shared core throttle does not
inspect, so this module extracts it here.

Both scopes read their rates from ``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]``
(``public_booking`` and ``public_booking_phone``); a throttle failure is rendered
by the central exception handler as a 429 with ``Retry-After``.
"""

from __future__ import annotations

import hashlib
from typing import Any

from rest_framework.throttling import BaseThrottle, SimpleRateThrottle


def _ip_ident(request: Any) -> str:
    return str(BaseThrottle().get_ident(request))


def _phone_ident(request: Any) -> str | None:
    data = getattr(request, "data", None)
    if not hasattr(data, "get"):
        return None
    candidate: Any = None
    patient = data.get("patient")
    if hasattr(patient, "get"):
        candidate = patient.get("phone") or patient.get("phone_e164")
    if not candidate:
        for name in ("phone", "phone_e164", "phone_number"):
            candidate = data.get(name)
            if candidate:
                break
    if not candidate:
        return None
    value = str(candidate).strip()
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PublicBookingIpThrottle(SimpleRateThrottle):
    """10 booking requests per minute per client IP."""

    scope = "public_booking"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": _ip_ident(request)}


class PublicBookingPhoneThrottle(SimpleRateThrottle):
    """3 booking requests per minute per submitted phone number."""

    scope = "public_booking_phone"

    def get_cache_key(self, request, view):
        ident = _phone_ident(request)
        if ident is None:
            # No phone in the request: this scope does not apply.
            return None
        return self.cache_format % {"scope": self.scope, "ident": ident}


class PublicBookingThrottle(BaseThrottle):
    """Composite: 10/min/IP and 3/min/phone (phone when the body carries one)."""

    def __init__(self) -> None:
        self.throttles = (PublicBookingIpThrottle(), PublicBookingPhoneThrottle())
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


__all__ = [
    "PublicBookingIpThrottle",
    "PublicBookingPhoneThrottle",
    "PublicBookingThrottle",
]
