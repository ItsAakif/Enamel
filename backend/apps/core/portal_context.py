"""Patient-portal request bootstrap (T137, ADR-013 decision D-3).

The portal is a **separate principal** from clinic staff. It gets its own
database role (``enamel_portal``) whose policies are patient-scoped, and its
own one-time-code bootstrap role (``enamel_portal_auth``) which may resolve a
clinic, resolve the patient behind a presented contact and read or write the
one-time-code challenge rows - and nothing else.

The bootstrap does three things, in this order:

1. it opens the request transaction, so every context value set below is
   transaction-local and cannot survive the request on a pooled connection;
2. it sets ``app.current_portal_key_hash`` to the SHA-256 of the
   ``enamel_portal`` cookie value (no database access), so the session row can
   be resolved *by principal* before a clinic context exists;
3. it switches the connection to the portal role with ``SET LOCAL ROLE``
   inside the request transaction and resets it in a ``finally`` block.

The role switch is **not** applied when the connection is a superuser: the
owner keeps the current behaviour, and the switch would be meaningless for a
role that bypasses the policies anyway. The switch is applied when the
current role is a non-superuser member of the portal role - which is exactly
the ``enamel_app`` runtime the shipped stack connects as - or when it is forced
with ``ENAMEL_PORTAL_DB_ROLE=1`` (the test suite runs the whole portal flow
under the portal role that way).

Every value the bootstrap sets is cleared again before the request returns.
"""

from __future__ import annotations

import hashlib
from typing import Any

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse

from .rls import (
    clear_rls_context,
    current_rls_role,
    reset_rls_role,
    set_rls_context,
    set_rls_role,
)

#: Every portal endpoint lives under this prefix.
PORTAL_API_PREFIX = "/api/v1/portal/"

#: The patient-scoped role the authenticated portal runs as.
PORTAL_ROLE = "enamel_portal"

#: The minimal role the unauthenticated one-time-code bootstrap runs as.
PORTAL_AUTH_ROLE = "enamel_portal_auth"

#: The endpoints that run as the one-time-code bootstrap role. Every other
#: portal endpoint runs as the authenticated patient role.
PORTAL_AUTH_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/portal/auth/request-code/",
        "/api/v1/portal/auth/verify/",
    }
)

#: ``auto`` (default), ``1``/``true`` (always switch) or ``0``/``false``
#: (never switch).
ROLE_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})
ROLE_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})


def portal_cookie_name() -> str:
    """The name of the portal session cookie."""

    return str(getattr(settings, "PORTAL_SESSION_COOKIE_NAME", "enamel_portal"))


def portal_key_hash(raw_key: str) -> str:
    """SHA-256 of the raw cookie value.

    The value must match ``apps.portal.services.identity.hash_key``; the raw
    token is never stored and never leaves the request.
    """

    return hashlib.sha256(str(raw_key or "").encode("utf-8")).hexdigest()


def is_portal_path(path: str) -> bool:
    """True when ``path`` is a portal endpoint."""

    return str(path or "").startswith(PORTAL_API_PREFIX)


def portal_role_for_path(path: str) -> str:
    """The database role a portal path runs as."""

    if str(path or "") in PORTAL_AUTH_PATHS:
        return PORTAL_AUTH_ROLE
    return PORTAL_ROLE


def role_switch_enabled() -> bool | None:
    """``True`` forces the switch, ``False`` forbids it, ``None`` is automatic."""

    raw = str(
        getattr(settings, "PORTAL_DB_ROLE_ENABLED", "auto") or "auto"
    ).strip().lower()
    if raw in ROLE_ENABLED_VALUES:
        return True
    if raw in ROLE_DISABLED_VALUES:
        return False
    return None


def _may_switch_to(role: str) -> bool:
    """True when the current connection may run as ``role``."""

    if not _can_query():
        return False
    with _cursor() as cursor:
        cursor.execute(
            "SELECT pg_has_role(current_user, %s, 'USAGE'), "
            "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user)",
            [role],
        )
        row = cursor.fetchone()
    if not row:
        return False
    has_role, is_superuser = bool(row[0]), bool(row[1])
    if not has_role:
        return False
    forced = role_switch_enabled()
    if forced is True:
        # A superuser may set any role; the forced mode is used by the test
        # suite to exercise the portal policies end to end.
        return True
    if forced is False:
        return False
    # Automatic mode: a superuser (the compose owner) keeps the current
    # behaviour, because it bypasses the policies anyway. The shipped runtime
    # is a non-superuser member of the portal role, so the switch applies to
    # it (ADR-013, T138).
    return not is_superuser


def _can_query() -> bool:
    from django.db import connection

    return connection.connection is not None and connection.vendor == "postgresql"


def _cursor():
    from django.db import connection

    return connection.cursor()


def switch_to_portal_role(role: str) -> tuple[str, str] | None:
    """Switch the connection to ``role``; returns the previous role pair."""

    if not _may_switch_to(role):
        return None
    return set_rls_role(role)


class PortalContextMiddleware:
    """Open the portal request transaction and establish the portal context."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not is_portal_path(request.path_info):
            return self.get_response(request)

        raw_cookie = request.COOKIES.get(portal_cookie_name(), "")
        role = portal_role_for_path(request.path_info)

        with transaction.atomic():
            # Any staff context that is already on the connection is dropped:
            # a portal request is a different principal.
            clear_rls_context()
            if raw_cookie:
                set_rls_context(portal_key_hash=portal_key_hash(raw_cookie))
            previous = switch_to_portal_role(role)
            try:
                return self.get_response(request)
            finally:
                reset_rls_role(previous)
                clear_rls_context()


def portal_context_available() -> dict[str, Any]:
    """Report the portal role configuration (used by the ops runbook)."""

    current, session = current_rls_role()
    return {
        "current_user": current,
        "session_user": session,
        "portal_role": PORTAL_ROLE,
        "portal_auth_role": PORTAL_AUTH_ROLE,
        "role_switch": role_switch_enabled(),
    }


__all__ = [
    "PORTAL_API_PREFIX",
    "PORTAL_AUTH_PATHS",
    "PORTAL_AUTH_ROLE",
    "PORTAL_ROLE",
    "PortalContextMiddleware",
    "is_portal_path",
    "portal_context_available",
    "portal_cookie_name",
    "portal_key_hash",
    "portal_role_for_path",
    "role_switch_enabled",
    "switch_to_portal_role",
]
