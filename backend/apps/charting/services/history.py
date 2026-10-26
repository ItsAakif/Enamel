"""Append-only odontogram history (T108).

The history table is the source of truth for *how* a chart reached its current
state, and it is deliberately boring: one row per mutation, oldest last when
read newest-first, and a ``before``/``after`` snapshot pair that is enough to
replay the whole chart. Nothing here ever updates or deletes a row -- the model
refuses both -- so the replay test is meaningful: it proves the snapshots are
complete, not that some other code path kept them in sync.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from django.db.models import Q

from apps.core.tenancy import with_clinic

from ..models import ToothState, ToothStateEvent

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def state_snapshot(state: ToothState) -> dict[str, Any]:
    """Full, JSON-safe snapshot of a tooth state (used for ``before``/``after``).

    This *does* include ``note``: it is the clinical history record for the
    tooth, access-controlled by ``charting.view`` exactly like the chart itself,
    and replaying it is what lets the history endpoint reconstruct the current
    state. The separate, redacted audit row never carries the note.
    """

    return {
        "id": str(state.id),
        "tooth": state.tooth,
        "dentition": state.dentition,
        "condition_code": state.condition_id,
        "status": state.status,
        "surfaces": list(state.surfaces or []),
        "severity": state.severity,
        "material": state.material,
        "note": state.note,
        "version": state.version,
        "plan_item_id": str(state.plan_item_id) if state.plan_item_id else None,
        "archived_at": state.archived_at.isoformat() if state.archived_at else None,
    }


def record_event(
    *,
    state: ToothState | None,
    patient: Any,
    event_type: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    version: int = 1,
    actor: Any = None,
    idempotency_key: str = "",
    batch_id: Any = None,
    occurred_at: Any = None,
) -> ToothStateEvent:
    """Insert one history row. The only write path into the table."""

    kwargs: dict[str, Any] = {
        "clinic": patient.clinic,
        "tooth_state": state,
        "patient": patient,
        "event_type": event_type,
        "before": before,
        "after": after,
        "actor": actor,
        "version": version,
        "idempotency_key": idempotency_key,
        "batch_id": batch_id,
    }
    # Only override the model default when the caller pins a timestamp, so a
    # None never reaches the NOT NULL column.
    if occurred_at is not None:
        kwargs["occurred_at"] = occurred_at

    with with_clinic(patient.clinic):
        event = ToothStateEvent.objects.create(**kwargs)
    return event


def history_queryset(patient: Any, tooth: int | None = None):
    """Newest-first, scoped queryset of a patient's history events."""

    queryset = ToothStateEvent.objects.filter(patient=patient).order_by(
        "-occurred_at", "-id"
    )
    if tooth is not None:
        queryset = queryset.filter(tooth_state__tooth=tooth)
    return queryset


def event_payload(event: ToothStateEvent) -> dict[str, Any]:
    after = event.after or {}
    return {
        "id": str(event.id),
        "tooth": after.get("tooth"),
        "event_type": event.event_type,
        "condition_code": after.get("condition_code"),
        "before": event.before,
        "after": event.after,
        "actor": str(event.actor_id) if event.actor_id else None,
        "occurred_at": event.occurred_at.isoformat(),
        "version": event.version,
        "batch_id": str(event.batch_id) if event.batch_id else None,
    }


def _encode_cursor(event: ToothStateEvent) -> str:
    raw = json.dumps(
        {"at": event.occurred_at.isoformat(), "id": str(event.id)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(raw: str) -> tuple[datetime, str]:
    """Decode a history cursor; raises ``ValueError`` when malformed."""

    padded = raw + "=" * (-len(raw) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        occurred_at = datetime.fromisoformat(str(payload["at"]))
        event_id = str(payload["id"])
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise ValueError("invalid cursor") from exc
    return occurred_at, event_id


def page_history(
    patient: Any,
    *,
    tooth: int | None = None,
    cursor: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> tuple[list[ToothStateEvent], str | None]:
    """Return ``(page, next_cursor)`` newest-first."""

    with with_clinic(patient.clinic):
        queryset = history_queryset(patient, tooth)
        if cursor:
            occurred_at, event_id = decode_cursor(cursor)
            queryset = queryset.filter(
                Q(occurred_at__lt=occurred_at)
                | Q(occurred_at=occurred_at, id__lt=event_id)
            )
        rows = list(queryset[: page_size + 1])

    has_more = len(rows) > page_size
    page = rows[:page_size]
    next_cursor = _encode_cursor(page[-1]) if has_more and page else None
    return page, next_cursor


def replay(events: Iterable[ToothStateEvent]) -> dict[tuple[int, str], dict[str, Any]]:
    """Reduce oldest-first events into the active state map.

    Keyed by ``(tooth, condition_code)``. An ``archived`` event (or any
    snapshot carrying ``archived_at``) removes the layer, which is exactly the
    semantics the unique-active constraint enforces in the table.
    """

    states: dict[tuple[int, str], dict[str, Any]] = {}
    for event in events:
        after = event.after or {}
        tooth = after.get("tooth")
        code = after.get("condition_code")
        if tooth is None or code is None:
            continue
        key = (int(tooth), str(code))
        if event.event_type == ToothStateEvent.EventType.ARCHIVED or after.get("archived_at"):
            states.pop(key, None)
        else:
            states[key] = after
    return states


def replay_patient(patient: Any, tooth: int | None = None) -> dict[tuple[int, str], dict[str, Any]]:
    """Replay every event for a patient (oldest first) into the active map."""

    with with_clinic(patient.clinic):
        queryset = ToothStateEvent.objects.filter(patient=patient).order_by(
            "occurred_at", "id"
        )
        if tooth is not None:
            queryset = queryset.filter(tooth_state__tooth=tooth)
        events = list(queryset)
    return replay(events)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "decode_cursor",
    "event_payload",
    "history_queryset",
    "page_history",
    "record_event",
    "replay",
    "replay_patient",
    "state_snapshot",
]
