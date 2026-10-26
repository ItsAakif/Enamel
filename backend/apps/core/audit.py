"""Append-only, per-clinic hash-chained audit log (T103).

``record`` is the single write path. It runs inside the caller's transaction so
a service can write its business mutation and the audit row atomically: if the
audit write fails, the whole transaction rolls back and the mutation is undone.

Chain construction
------------------

Events are chained per clinic (platform-scoped events share the ``platform``
chain). The chain is serialised with a transaction-scoped PostgreSQL advisory
lock keyed by ``hashtext('audit:<clinic|platform>')``, so two concurrent
writers cannot read the same ``prev_hash`` and produce two children. The lock
is held until the surrounding transaction commits.

``hash = sha256(prev_hash + canonical_json)`` where ``canonical_json`` is the
event payload serialised with sorted keys, ``(",", ":")`` separators and no
whitespace. ``core.ledger``-style verification (``manage.py audit_verify``)
recomputes the same digest from stored rows.

The payload deliberately includes ``created_at`` and ``actor_id`` so that
tampering with a timestamp or an actor is detected too. Because of that both
foreign keys use ``on_delete=PROTECT``: nothing may silently rewrite a hashed
column after the fact.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import nullcontext
from typing import Any

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from .models import AuditEvent
from .redaction import redact
from .rls import rls_context
from .tenancy import current_scope

PLATFORM_CHAIN = "platform"

#: The database helper that reads the previous hash of a chain with the table
#: owner's rights. It is created by ``core/migrations/0006_principal_scoped_rls.py``
#: and lets a writer that may not read the audit table - the patient portal -
#: append to the hash chain without reading the chain.
AUDIT_HELPER_NAME = "enamel_audit_prev_hash"

#: Cached availability of the helper (``None`` = not probed yet).
_audit_helper_state: bool | None = None


def _audit_helper_available() -> bool:
    """True when the audit-chain helper exists in this database.

    A database that is still at migration ``0005`` does not have the helper
    yet; the chain read then falls back to the ORM query.
    """

    global _audit_helper_state
    if _audit_helper_state is True:
        return True
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regprocedure(%s) IS NOT NULL",
                [f"{AUDIT_HELPER_NAME}(uuid)"],
            )
            row = cursor.fetchone()
        available = bool(row and row[0])
    except DatabaseError:  # pragma: no cover - connection already failing
        available = False
    if available:
        _audit_helper_state = True
    return available


def previous_hash(clinic_id: Any) -> str:
    """The hash of the last event of a chain (empty when the chain is empty).

    The read goes through the database helper when it exists, because the
    writer of an audit event may not be allowed to read the audit table (the
    patient portal writes audit events but may not read the staff audit
    chain).
    """

    if _audit_helper_available():
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT {AUDIT_HELPER_NAME}(%s)", [clinic_id])
            row = cursor.fetchone()
        return str(row[0]) if row and row[0] else ""
    return (
        AuditEvent.objects.filter(clinic_id=clinic_id)
        .order_by("-id")
        .values_list("hash", flat=True)
        .first()
    ) or ""


def chain_key(clinic_id: Any) -> str:
    """Return the advisory-lock / chain key for a clinic (or the platform)."""

    return str(clinic_id) if clinic_id else PLATFORM_CHAIN


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _clinic_id_from(value: Any) -> uuid.UUID | None:
    """Best-effort clinic id from a Clinic, a tenant model, or a UUID.

    Only a ``Clinic`` instance may fall back to its own pk: a ``User`` or any
    other non-tenant model must never be mistaken for a clinic.
    """

    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    clinic_id = getattr(value, "clinic_id", None)
    if clinic_id is not None:
        return _as_uuid(clinic_id)
    meta = getattr(value, "_meta", None)
    if meta is not None and getattr(meta, "label_lower", "") == "clinics.clinic":
        return _as_uuid(getattr(value, "pk", None))
    return None


def resolve_single_clinic(user: Any) -> uuid.UUID | None:
    """Return the user's clinic id when they have exactly one active membership.

    Used by auth entry points (login, logout, password reset) that run before a
    clinic scope exists. With zero or several memberships the clinic is
    ambiguous, so the caller decides whether to skip the audit event.

    The membership rows are addressed by the principal key, so the principal
    context is opened for the read (ADR-013).
    """

    if user is None or not getattr(user, "is_authenticated", False):
        return None
    from django.apps import apps as django_apps

    from .rls import rls_context

    membership_model = django_apps.get_model("clinics", "Membership")
    # The read runs inside its own transaction, so the principal context is
    # transaction-local and cannot be separated from the read on a pooled
    # connection (the same rule as ``accounts.services.auth.active_memberships``).
    with transaction.atomic():
        with rls_context(None, principal_id=getattr(user, "pk", None)):
            clinic_ids = list(
                membership_model.all_objects.filter(user=user, status="active")
                .values_list("clinic_id", flat=True)[:2]
            )
    if len(clinic_ids) == 1:
        return clinic_ids[0]
    return None


def _actor_from(request: Any) -> Any | None:
    user = getattr(request, "user", None)
    # Only a persisted auth model may be an audit actor: ``actor_id`` is a FK to
    # ``accounts.User``. The patient portal authenticates a non-model principal
    # (``portal.authentication.PortalPrincipal``), which must not be mistaken
    # for a user here.
    if (
        user is not None
        and getattr(user, "is_authenticated", False)
        and hasattr(user, "_meta")
    ):
        return user
    return None


def _request_id(request: Any) -> str:
    return str(getattr(request, "request_id", "") or "")


def _client_ip(request: Any) -> str:
    meta = getattr(request, "META", None)
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("REMOTE_ADDR") or "")


def hash_ip(ip: str | None) -> str:
    """Return a salted SHA-256 of ``ip`` (never the raw address)."""

    if not ip:
        return ""
    salt = str(getattr(settings, "SECRET_KEY", "") or "")
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()


def canonical_payload(
    *,
    action: str,
    clinic_id: Any,
    actor_id: Any,
    entity_type: str | None,
    entity_id: str | None,
    before: Any,
    after: Any,
    request_id: str | None,
    ip_hash: str | None,
    created_at,
) -> str:
    """Serialise the hashed part of an audit event deterministically."""

    payload = {
        "action": action,
        "actor_id": str(actor_id) if actor_id else None,
        "after": after,
        "before": before,
        "clinic_id": str(clinic_id) if clinic_id else None,
        "created_at": created_at.isoformat(),
        "entity_id": entity_id or "",
        "entity_type": entity_type or "",
        "ip_hash": ip_hash or "",
        "request_id": request_id or "",
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def compute_hash(prev_hash: str, canonical: str) -> str:
    """Return ``sha256(prev_hash + canonical)`` as a hex digest."""

    return hashlib.sha256(((prev_hash or "") + canonical).encode("utf-8")).hexdigest()


def _entity_defaults(entity: Any) -> tuple[str, str]:
    if entity is None:
        return "", ""
    meta = getattr(entity, "_meta", None)
    entity_type = getattr(meta, "label_lower", "") if meta is not None else ""
    pk = getattr(entity, "pk", None)
    return str(entity_type), "" if pk is None else str(pk)


def record(
    action: str,
    entity: Any | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    actor: Any | None = None,
    clinic: Any | None = None,
    request: Any | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> AuditEvent:
    """Append one audit event and return it.

    ``action`` uses the ``<domain>.<verb>`` format (``clinic.updated``,
    ``membership.role_changed``). The clinic is resolved from, in order: the
    explicit ``clinic`` argument, ``entity.clinic_id`` (or the entity's own pk
    for a Clinic), then the active clinic scope. Platform events carry no
    clinic and join the ``platform`` chain.
    """

    resolved_actor = actor if actor is not None else _actor_from(request)

    resolved_clinic_id = _clinic_id_from(clinic)
    if resolved_clinic_id is None:
        resolved_clinic_id = _clinic_id_from(entity)
    if resolved_clinic_id is None:
        scope = current_scope()
        resolved_clinic_id = scope.clinic_id if scope is not None else None

    default_type, default_id = _entity_defaults(entity)
    final_type = entity_type if entity_type is not None else default_type
    final_id = entity_id if entity_id is not None else default_id

    redacted_before = None if before is None else redact(before)
    redacted_after = None if after is None else redact(after)

    actor_id = getattr(resolved_actor, "pk", None)
    request_id = _request_id(request)
    ip_value = hash_ip(_client_ip(request))

    # ``atomic`` joins the caller's transaction when one is active (the audit
    # then commits or rolls back with the mutation) and creates one otherwise,
    # which is what makes the advisory lock meaningful for isolated callers.
    #
    # The write opens the clinic context of the row it appends: the audit
    # table is addressed by the clinic, and a writer that may not read the
    # audit chain (the patient portal) still has to be able to append to it.
    with rls_context(resolved_clinic_id) if resolved_clinic_id else nullcontext():
        with transaction.atomic():
            lock_key = f"audit:{chain_key(resolved_clinic_id)}"
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)",
                    [lock_key],
                )

            prev_hash = previous_hash(resolved_clinic_id)

            created_at = timezone.now()
            canonical = canonical_payload(
                action=action,
                clinic_id=resolved_clinic_id,
                actor_id=actor_id,
                entity_type=final_type,
                entity_id=final_id,
                before=redacted_before,
                after=redacted_after,
                request_id=request_id,
                ip_hash=ip_value,
                created_at=created_at,
            )
            digest = compute_hash(prev_hash, canonical)

            event = AuditEvent(
                clinic_id=resolved_clinic_id,
                actor_id=actor_id,
                action=action,
                entity_type=final_type,
                entity_id=final_id,
                before=redacted_before,
                after=redacted_after,
                request_id=request_id,
                ip_hash=ip_value,
                prev_hash=prev_hash,
                hash=digest,
                created_at=created_at,
            )
            _append_event(event)
            return event


def _append_event(event: AuditEvent) -> None:
    """Insert the audit row and set its primary key.

    The insert is issued **without** a ``RETURNING`` clause on purpose.
    ``INSERT ... RETURNING`` makes PostgreSQL apply the *SELECT* policies to
    the inserted row, so every writer would have to be allowed to read the
    audit table - which is exactly what the class-X inventory forbids (a
    tenant may append a platform audit row but never read one, and the
    patient portal may append an audit row but never read the staff audit
    chain). The generated id is read back from the sequence instead.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO core_auditevent ("
            "clinic_id, actor_id, action, entity_type, entity_id, before, after, "
            "request_id, ip_hash, prev_hash, hash, created_at"
            ") VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)",
            [
                event.clinic_id,
                event.actor_id,
                event.action,
                event.entity_type,
                event.entity_id,
                None if event.before is None else json.dumps(event.before),
                None if event.after is None else json.dumps(event.after),
                event.request_id,
                event.ip_hash,
                event.prev_hash,
                event.hash,
                event.created_at,
            ],
        )
        cursor.execute("SELECT currval('core_auditevent_id_seq')")
        row = cursor.fetchone()
    event.pk = int(row[0]) if row and row[0] is not None else None


def verify_chain(clinic_id: Any = None) -> tuple[bool, int, int | None]:
    """Recompute a chain and report the first inconsistency.

    Returns ``(ok, verified_count, first_broken_id)``. ``clinic_id`` may be a
    UUID, a Clinic instance, or None for the platform chain.
    """

    resolved = _clinic_id_from(clinic_id)
    prev_hash = ""
    count = 0
    for event in AuditEvent.objects.filter(clinic_id=resolved).order_by("id").iterator():
        canonical = canonical_payload(
            action=event.action,
            clinic_id=event.clinic_id,
            actor_id=event.actor_id,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            before=event.before,
            after=event.after,
            request_id=event.request_id,
            ip_hash=event.ip_hash,
            created_at=event.created_at,
        )
        expected = compute_hash(prev_hash, canonical)
        if event.prev_hash != prev_hash or event.hash != expected:
            return False, count, event.pk
        prev_hash = event.hash
        count += 1
    return True, count, None


__all__ = [
    "PLATFORM_CHAIN",
    "canonical_payload",
    "chain_key",
    "compute_hash",
    "hash_ip",
    "record",
    "resolve_single_clinic",
    "verify_chain",
]
