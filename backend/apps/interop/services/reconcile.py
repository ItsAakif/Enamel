"""Conflict queuing and resolution (T130).

A conflict is raised when an external id resolves to a *different* internal row
than the one already mapped. The sync calls :func:`queue_conflict` and moves on:
it never rewrites the mapping and never touches the conflicting internal row, so
a bad PMS payload cannot silently corrupt Enamel data.

Queuing is idempotent: re-running a sync with the same conflict does not create a
second open row for the same ``(connector, entity_type, external_id)``.
"""

from __future__ import annotations

from typing import Any

from ..models import ConflictStatus, SyncConflict

REASON_INTERNAL_MISMATCH = "internal_id_mismatch"
REASON_EXTERNAL_REUSED = "external_id_reused"


def queue_conflict(
    connector: Any,
    *,
    entity_type: str,
    external_id: str,
    internal_id: Any = None,
    reason: str = REASON_INTERNAL_MISMATCH,
) -> SyncConflict:
    """Queue (idempotently) a conflict for human review."""

    key = str(external_id or "").strip()
    existing = SyncConflict.all_objects.filter(
        connector=connector,
        entity_type=entity_type,
        external_id=key,
        status=ConflictStatus.OPEN,
    ).first()
    if existing is not None:
        return existing

    return SyncConflict.all_objects.create(
        clinic_id=connector.clinic_id,
        connector=connector,
        entity_type=entity_type,
        external_id=key,
        internal_id=internal_id,
        reason=reason,
        status=ConflictStatus.OPEN,
    )


def open_conflicts(connector: Any) -> list[SyncConflict]:
    return list(
        SyncConflict.all_objects.filter(
            connector=connector, status=ConflictStatus.OPEN
        ).order_by("-created_at", "-id")
    )


def resolve_conflict(
    conflict: SyncConflict,
    *,
    internal_id: Any = None,
    status: str = ConflictStatus.RESOLVED,
) -> SyncConflict:
    """Close a conflict, optionally recording the chosen internal row."""

    if internal_id is not None:
        conflict.internal_id = internal_id
    conflict.status = status
    conflict.save(update_fields=["internal_id", "status", "updated_at"])
    return conflict


__all__ = [
    "REASON_EXTERNAL_REUSED",
    "REASON_INTERNAL_MISMATCH",
    "open_conflicts",
    "queue_conflict",
    "resolve_conflict",
]
