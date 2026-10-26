"""External id mapping helpers (T130).

The :class:`~apps.interop.models.ExternalIdMap` row is the idempotency key for a
connector: a second sync of the same external record finds the mapping and skips
the create. ``create_mapping`` is race-safe (``get_or_create`` under the unique
constraint), so two concurrent runs cannot create two internal rows for one
external id.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..models import ExternalIdMap


def get_mapping(
    connector: Any, entity_type: str, external_id: str
) -> ExternalIdMap | None:
    """Return the mapping for ``(connector, entity_type, external_id)`` or None."""

    key = str(external_id or "").strip()
    if not key:
        return None
    return ExternalIdMap.all_objects.filter(
        connector=connector, entity_type=entity_type, external_id=key
    ).first()


def internal_id_for(connector: Any, entity_type: str, external_id: str) -> uuid.UUID | None:
    mapping = get_mapping(connector, entity_type, external_id)
    return mapping.internal_id if mapping is not None else None


def create_mapping(
    connector: Any,
    entity_type: str,
    external_id: str,
    internal_id: Any,
) -> tuple[ExternalIdMap, bool]:
    """Create (or fetch) the mapping; returns ``(mapping, created)``."""

    key = str(external_id or "").strip()
    if not key:
        raise ValueError("An external id is required.")
    return ExternalIdMap.all_objects.get_or_create(
        connector=connector,
        entity_type=entity_type,
        external_id=key,
        defaults={
            "clinic_id": connector.clinic_id,
            "internal_id": internal_id,
        },
    )


def mappings_for(connector: Any, entity_type: str) -> list[ExternalIdMap]:
    return list(
        ExternalIdMap.all_objects.filter(
            connector=connector, entity_type=entity_type
        ).order_by("external_id", "id")
    )


def delete_mapping(connector: Any, entity_type: str, external_id: str) -> int:
    """Remove a mapping (used by tests and a future unlink workflow)."""

    return ExternalIdMap.all_objects.filter(
        connector=connector, entity_type=entity_type, external_id=str(external_id)
    ).delete()[0]


__all__ = [
    "create_mapping",
    "delete_mapping",
    "get_mapping",
    "internal_id_for",
    "mappings_for",
]
