"""PostgreSQL row-level-security context helpers (T135, T137).

The database policies created by ``core/migrations/0005_rls.py`` and
``core/migrations/0006_principal_scoped_rls.py`` read custom GUCs. Every one of
them is written with ``SET LOCAL`` semantics inside a transaction, so a value
can never survive the request (or the job) that set it on a pooled connection.

``app.current_clinic_id``
    The clinic whose class-T rows are visible. An empty/unset value matches no
    tenant row.
``app.current_user_id``
    The acting user (audit/debugging only; policies do not use it).
``app.current_principal_id``
    The authenticated staff user, set by ``ClinicScopeMiddleware`` *before* the
    clinic is resolved. A principal with no clinic yet (login, invitation
    accept) still has to be able to read its own rows.
``app.current_invitation_token_hash``
    SHA-256 of the presented invitation token (the capability that resolves an
    invitation before a clinic context exists).
``app.current_portal_key_hash``
    SHA-256 of the ``enamel_portal`` cookie value. The portal session is
    resolved by this hash, with no clinic context.
``app.current_portal_patient_id``
    The patient resolved from the portal session or from the one-time-code
    challenge.
``app.current_portal_challenge_id``
    The one-time-code challenge presented by the caller.
``app.current_portal_contact``
    The normalized contact (email or E.164 phone) presented to the
    one-time-code bootstrap. It is the capability that resolves the patient
    inside the named clinic, so it never leaves the transaction.
``app.current_webhook_gateway``
    The gateway segment of the payment webhook URL.
``app.current_webhook_event_id``
    The ``event_id`` of the signed webhook payload.
``app.current_webhook_reference``
    The ``gateway_payment_id`` of the signed webhook payload.
``app.bypass_rls``
    ``'on'`` while migrations, seed commands, audit maintenance or platform
    sweepers run. The policy that honours it is granted to the table owner and
    to the maintenance role only, so setting the GUC is not an escape hatch for
    the application role.

The policies themselves are **enforced at runtime**: the ``api`` service
connects as ``enamel_app`` and the ``worker``/``beat`` as ``enamel_worker``,
both ``NOSUPERUSER``/``NOBYPASSRLS`` without DDL/DCL rights and not members
of the owner or of any bypass holder (ADR-013).

Outside a transaction ``set_config`` is session-scoped and explicitly restored
by the context managers below. That keeps a pooled connection from carrying one
clinic's context into the next request.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from django.db import DatabaseError, connection

CLINIC_SETTING = "app.current_clinic_id"
USER_SETTING = "app.current_user_id"
BYPASS_SETTING = "app.bypass_rls"
PRINCIPAL_SETTING = "app.current_principal_id"
INVITATION_HASH_SETTING = "app.current_invitation_token_hash"
PORTAL_KEY_HASH_SETTING = "app.current_portal_key_hash"
PORTAL_PATIENT_SETTING = "app.current_portal_patient_id"
PORTAL_CHALLENGE_SETTING = "app.current_portal_challenge_id"
PORTAL_CONTACT_SETTING = "app.current_portal_contact"
WEBHOOK_GATEWAY_SETTING = "app.current_webhook_gateway"
WEBHOOK_EVENT_SETTING = "app.current_webhook_event_id"
WEBHOOK_REFERENCE_SETTING = "app.current_webhook_reference"

#: Every context GUC the policies may read. ``clear_rls_context`` resets all of
#: them, which is how a request or a job proves that it leaves no residue.
RLS_SETTINGS: tuple[str, ...] = (
    CLINIC_SETTING,
    USER_SETTING,
    BYPASS_SETTING,
    PRINCIPAL_SETTING,
    INVITATION_HASH_SETTING,
    PORTAL_KEY_HASH_SETTING,
    PORTAL_PATIENT_SETTING,
    PORTAL_CHALLENGE_SETTING,
    PORTAL_CONTACT_SETTING,
    WEBHOOK_GATEWAY_SETTING,
    WEBHOOK_EVENT_SETTING,
    WEBHOOK_REFERENCE_SETTING,
)

#: A role name is never parameterised (PostgreSQL does not allow it), so it is
#: validated against this pattern before it is interpolated into a statement.
_ROLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: The runtime roles of ADR-013. A platform maintenance command refuses to run
#: as one of them: it would silently see nothing across the clinic boundary.
RUNTIME_ROLES: tuple[str, ...] = (
    "enamel_app",
    "enamel_worker",
    "enamel_platform",
    "enamel_portal",
    "enamel_portal_auth",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


def _can_touch_database() -> bool:
    """Return True when a PostgreSQL connection is already open.

    ``with_clinic`` is used by a few pure unit tests without the ``db`` marker.
    Opening a connection there would raise pytest-django's database-access
    guard, so the helper is a no-op until something else has connected. The
    middleware and job runner set the context explicitly, so this only affects
    service-level calls.
    """

    return connection.connection is not None and connection.vendor == "postgresql"


def set_rls_context(
    clinic_id: Any = None,
    user_id: Any = None,
    *,
    principal_id: Any = None,
    invitation_token_hash: str = "",
    portal_key_hash: str = "",
    portal_patient_id: Any = None,
    portal_challenge_id: Any = None,
    portal_contact: str = "",
    webhook_gateway: str = "",
    webhook_event_id: str = "",
    webhook_reference: str = "",
    bypass: bool | None = None,
) -> dict[str, str | None]:
    """Set the RLS GUCs and return their previous values.

    ``bypass=None`` leaves the bypass GUC untouched. The value is local to the
    current transaction when one is open and session-scoped otherwise.

    The two positional arguments keep their original meaning: ``clinic_id`` and
    ``user_id`` are applied whenever they are not ``None`` (an empty
    ``user_id`` clears the user GUC). Every new argument is keyword-only and
    defaults to "unset": a ``None`` id or an empty string leaves that GUC
    alone. Use :func:`clear_rls_context` to reset the whole set.
    """

    previous: dict[str, str | None] = {}
    if not _can_touch_database():
        return previous

    local = connection.in_atomic_block
    values: dict[str, str] = {}
    if clinic_id is not None:
        values[CLINIC_SETTING] = _text(clinic_id)
    if user_id is not None:
        values[USER_SETTING] = _text(user_id)
    if principal_id is not None:
        values[PRINCIPAL_SETTING] = _text(principal_id)
    if invitation_token_hash:
        values[INVITATION_HASH_SETTING] = _text(invitation_token_hash)
    if portal_key_hash:
        values[PORTAL_KEY_HASH_SETTING] = _text(portal_key_hash)
    if portal_patient_id is not None:
        values[PORTAL_PATIENT_SETTING] = _text(portal_patient_id)
    if portal_challenge_id is not None:
        values[PORTAL_CHALLENGE_SETTING] = _text(portal_challenge_id)
    if portal_contact:
        values[PORTAL_CONTACT_SETTING] = _text(portal_contact)
    if webhook_gateway:
        values[WEBHOOK_GATEWAY_SETTING] = _text(webhook_gateway)
    if webhook_event_id:
        values[WEBHOOK_EVENT_SETTING] = _text(webhook_event_id)
    if webhook_reference:
        values[WEBHOOK_REFERENCE_SETTING] = _text(webhook_reference)
    if bypass is not None:
        values[BYPASS_SETTING] = "on" if bypass else ""

    for name, value in values.items():
        # One round trip per setting: capture the previous value in a
        # materialised CTE, then apply the new one. Keeping this constant
        # matters for query-count assertions in service tests.
        with connection.cursor() as cursor:
            cursor.execute(
                "WITH previous AS MATERIALIZED ("
                "  SELECT current_setting(%s, true) AS value"
                ") "
                "SELECT previous.value, set_config(%s, %s, %s) FROM previous",
                [name, name, value, local],
            )
            row = cursor.fetchone()
        previous[name] = row[0] if row else None
    return previous


def restore_rls_context(previous: dict[str, str | None]) -> None:
    """Restore values captured by :func:`set_rls_context`."""

    if not previous or not _can_touch_database():
        return
    if getattr(connection, "needs_rollback", False):
        # The transaction is already doomed; LOCAL values disappear on rollback.
        return

    local = connection.in_atomic_block
    for name, value in previous.items():
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config(%s, %s, %s)", [name, value or "", local]
                )
        except DatabaseError:  # pragma: no cover - connection already failing
            return


def clear_rls_context() -> None:
    """Reset every context GUC to "not set" in one round trip.

    A request or a job calls this when it is done: a pooled connection must
    never carry a clinic, a principal, a portal key hash or a webhook
    capability into the next task.
    """

    if not _can_touch_database():
        return
    if getattr(connection, "needs_rollback", False):
        return
    local = connection.in_atomic_block
    assignments = ", ".join("set_config(%s, '', %s)" for _ in RLS_SETTINGS)
    params: list[Any] = []
    for name in RLS_SETTINGS:
        params.extend([name, local])
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT {assignments}", params)
    except DatabaseError:  # pragma: no cover - connection already failing
        return


@contextmanager
def rls_context(
    clinic_id: Any = None,
    user_id: Any = None,
    *,
    principal_id: Any = None,
    invitation_token_hash: str = "",
    portal_key_hash: str = "",
    portal_patient_id: Any = None,
    portal_challenge_id: Any = None,
    portal_contact: str = "",
    webhook_gateway: str = "",
    webhook_event_id: str = "",
    webhook_reference: str = "",
) -> Iterator[None]:
    """Run a block with one request's RLS context active.

    ``user_id=None`` leaves an existing user setting untouched; pass an empty
    string to clear it. Every keyword argument has the same "unset" meaning as
    in :func:`set_rls_context`.
    """

    previous = set_rls_context(
        clinic_id,
        user_id,
        principal_id=principal_id,
        invitation_token_hash=invitation_token_hash,
        portal_key_hash=portal_key_hash,
        portal_patient_id=portal_patient_id,
        portal_challenge_id=portal_challenge_id,
        portal_contact=portal_contact,
        webhook_gateway=webhook_gateway,
        webhook_event_id=webhook_event_id,
        webhook_reference=webhook_reference,
    )
    try:
        yield
    finally:
        restore_rls_context(previous)


@contextmanager
def bypass_rls() -> Iterator[None]:
    """Run a block with the explicit maintenance bypass policy active.

    Reserved for migrations, seed/audit commands and platform sweepers that must
    cross clinic boundaries. Request and durable-job handlers use
    :func:`rls_context` instead. The bypass only has an effect for a role the
    ``clinic_bypass`` policy is granted to (the table owner and the
    maintenance role), so the application role cannot use it.
    """

    previous = set_rls_context(bypass=True)
    try:
        yield
    finally:
        restore_rls_context(previous)


# --- database role switching ------------------------------------------------


def _quote_role(role: str) -> str:
    name = str(role or "").strip()
    if not _ROLE_NAME_RE.match(name):
        raise ValueError(f"Invalid database role name: {role!r}")
    return '"' + name.replace('"', '""') + '"'


def current_rls_role() -> tuple[str, str]:
    """Return ``(current_user, session_user)`` for the connection."""

    if not _can_touch_database():
        return ("", "")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user, session_user")
        row = cursor.fetchone()
    if not row:
        return ("", "")
    return (str(row[0] or ""), str(row[1] or ""))


def bypass_capable() -> bool:
    """True when the current role may use the maintenance bypass.

    The bypass policy (``clinic_bypass``) is granted to the table owner and
    to the maintenance role only, so this is the check that a platform
    maintenance command really runs with the rights it needs.
    """

    if not _can_touch_database():
        return False
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM pg_policies AS p
                  JOIN pg_roles AS r ON r.rolname = ANY (p.roles)
                 WHERE p.schemaname = current_schema()
                   AND p.policyname = 'clinic_bypass'
                   AND r.rolname = current_user
            )
            """
        )
        row = cursor.fetchone()
    return bool(row and row[0])


def require_bypass_capable(command: str) -> None:
    """Refuse an owner-only command that runs as a runtime role.

    The platform maintenance commands (``audit_verify``,
    ``apply_patient_retention``, ``seed_demo``, ``seed_rcm_fixtures``) cross
    clinic boundaries on purpose. They must run with the maintenance role
    (``enamel_maint``) or with the owner; under a **runtime** role they would
    silently see nothing, so they are refused loudly instead (ADR-013,
    decision D-2).

    A role that is not a runtime role (the owner, the maintenance role, the
    platform-admin role, the test fixture role) may run them.
    """

    current, _session = current_rls_role()
    if current not in RUNTIME_ROLES:
        return
    if bypass_capable():
        return
    raise RuntimeError(
        f"'{command}' is a platform maintenance command and needs the "
        f"maintenance role (enamel_maint) or the owner: the current role "
        f"'{current}' is a runtime role and does not hold the "
        f"row-level-security bypass. Run it with the ops profile "
        f"(docker compose --profile ops run --rm ops {command})."
    )


def set_rls_role(role: str) -> tuple[str, str]:
    """Switch the connection to ``role`` for the rest of the transaction.

    ``SET LOCAL ROLE`` is used so the switch can never outlive the
    transaction, even if the process dies. The previous
    ``(current_user, session_user)`` pair is returned so the caller can put
    it back with :func:`reset_rls_role`.
    """

    if not _can_touch_database():
        return ("", "")
    previous = current_rls_role()
    with connection.cursor() as cursor:
        cursor.execute(f"SET LOCAL ROLE {_quote_role(role)}")
    return previous


def reset_rls_role(previous: tuple[str, str] | None = None) -> None:
    """Undo :func:`set_rls_role`.

    ``RESET ROLE`` drops the transaction-local role and returns to the session
    user; the previous role is then re-established when it was not the session
    user (a test or a maintenance command may have switched it deliberately).
    """

    if not _can_touch_database():
        return
    if getattr(connection, "needs_rollback", False):
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
    except DatabaseError:  # pragma: no cover - connection already failing
        return
    if not previous:
        return
    current, session = previous
    if current and session and current != session:
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"SET ROLE {_quote_role(current)}")
        except DatabaseError:  # pragma: no cover - connection already failing
            return


__all__ = [
    "BYPASS_SETTING",
    "CLINIC_SETTING",
    "INVITATION_HASH_SETTING",
    "PORTAL_CHALLENGE_SETTING",
    "PORTAL_CONTACT_SETTING",
    "PORTAL_KEY_HASH_SETTING",
    "PORTAL_PATIENT_SETTING",
    "PRINCIPAL_SETTING",
    "RLS_SETTINGS",
    "USER_SETTING",
    "WEBHOOK_EVENT_SETTING",
    "WEBHOOK_GATEWAY_SETTING",
    "WEBHOOK_REFERENCE_SETTING",
    "bypass_capable",
    "bypass_rls",
    "clear_rls_context",
    "current_rls_role",
    "require_bypass_capable",
    "reset_rls_role",
    "restore_rls_context",
    "rls_context",
    "set_rls_context",
    "set_rls_role",
]
