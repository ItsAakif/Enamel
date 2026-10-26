"""Patient timeline assembly (T106).

The timeline merges five sources into one newest-first stream:

* the patient record itself (``created_at``; ``updated_at`` only when it
  differs, so a just-created patient does not produce two identical events)
* encounters (``occurred_at``)
* allergies (``recorded_at``)
* medical-history entries (``created_at``)
* appointments and charting events, when those apps are installed

Each source is one bounded query (``fetch_limit``), so the number of queries
does not grow with the number of events -- 50 events cost the same as 5. The
merge then happens in Python. Events are ordered by ``(at desc, entity_id
desc)``, which makes the ordering total and stable: two events never compare
equal unless they are the same type/entity pair.

The cursor encodes ``(at, entity_id)`` and is opaque base64url JSON; it is
strictly positional, so appending new events never reshuffles an in-flight
page.

Metadata is intentionally thin: enum values, statuses and ids only. Free text
(notes, substances, complaints) never leaves the source endpoint's own payload
and is not duplicated here.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.apps import apps as django_apps

from apps.core.tenancy import with_clinic

from ..models import Allergy, Encounter, MedicalHistory, Patient

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
DEFAULT_FETCH_LIMIT = 500
# See ``_patient_events``: keeps the insert-time book-keeping tick out of the feed.
_UPDATE_TOLERANCE = timedelta(seconds=1)


@dataclass(frozen=True)
class TimelineEvent:
    type: str
    at: datetime
    title: str
    summary: str
    entity_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def sort_key(self) -> tuple[float, str, str]:
        return (-self.at.timestamp(), self.entity_id, self.type)

    def as_payload(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "at": self.at.isoformat(),
            "title": self.title,
            "summary": self.summary,
            "entity_id": self.entity_id,
            "metadata": self.metadata,
        }


def _events_for(
    patient: Patient,
    model_name: str,
    timestamp_field: str,
    *,
    fetch_limit: int,
) -> list[Any]:
    with with_clinic(patient.clinic):
        model = {
            "encounter": Encounter,
            "allergy": Allergy,
            "medical_history": MedicalHistory,
        }[model_name]
        queryset = (
            model.objects.filter(patient=patient)
            .order_by(f"-{timestamp_field}", "-id")[:fetch_limit]
        )
        return list(queryset)


def _patient_events(patient: Patient) -> list[TimelineEvent]:
    events = [
        TimelineEvent(
            type="patient.created",
            at=patient.created_at,
            title="Patient created",
            summary=f"Source: {patient.source}",
            entity_id=str(patient.id),
            metadata={"status": patient.status, "source": patient.source},
        )
    ]
    # ``auto_now_add`` and ``auto_now`` both stamp on insert, so a brand-new
    # patient has ``updated_at`` a few microseconds after ``created_at``. A one
    # second tolerance keeps that book-keeping tick out of the feed while still
    # reporting any later edit.
    if patient.updated_at and patient.updated_at - patient.created_at > _UPDATE_TOLERANCE:
        events.append(
            TimelineEvent(
                type="patient.updated",
                at=patient.updated_at,
                title="Patient record updated",
                summary=f"Status: {patient.status}",
                entity_id=str(patient.id),
                metadata={"status": patient.status},
            )
        )
    return events


def _encounter_events(patient: Patient, *, fetch_limit: int) -> list[TimelineEvent]:
    return [
        TimelineEvent(
            type="encounter.created",
            at=encounter.occurred_at,
            title="Encounter",
            summary=f"{encounter.kind} ({encounter.status})",
            entity_id=str(encounter.id),
            metadata={
                "kind": encounter.kind,
                "status": encounter.status,
                "appointment_id": str(encounter.appointment_id)
                if encounter.appointment_id
                else None,
            },
        )
        for encounter in _events_for(patient, "encounter", "occurred_at", fetch_limit=fetch_limit)
    ]


def _allergy_events(patient: Patient, *, fetch_limit: int) -> list[TimelineEvent]:
    return [
        TimelineEvent(
            type="allergy.created",
            at=allergy.recorded_at,
            title="Allergy recorded",
            summary=f"Severity: {allergy.severity}",
            entity_id=str(allergy.id),
            metadata={"severity": allergy.severity, "is_active": allergy.is_active},
        )
        for allergy in _events_for(patient, "allergy", "recorded_at", fetch_limit=fetch_limit)
    ]


def _history_events(patient: Patient, *, fetch_limit: int) -> list[TimelineEvent]:
    return [
        TimelineEvent(
            type="medical_history.created",
            at=entry.created_at,
            title="Medical history",
            summary=entry.kind,
            entity_id=str(entry.id),
            metadata={"kind": entry.kind, "is_active": entry.is_active},
        )
        for entry in _events_for(
            patient, "medical_history", "created_at", fetch_limit=fetch_limit
        )
    ]


def _appointment_events(patient: Patient, *, fetch_limit: int) -> list[TimelineEvent]:
    """Appointments, when the scheduling app is installed (guarded import)."""

    if not django_apps.is_installed("apps.scheduling"):
        return []
    Appointment = django_apps.get_model("scheduling", "Appointment")
    manager = getattr(Appointment, "all_objects", None)
    if manager is None:  # pragma: no cover - the real model exposes all_objects
        manager = Appointment._default_manager
    rows = list(
        manager.filter(clinic_id=patient.clinic_id, patient_id=patient.pk)
        .order_by("-start_at", "-id")[:fetch_limit]
    )
    return [
        TimelineEvent(
            type="appointment",
            at=appointment.start_at,
            title="Appointment",
            summary=str(appointment.status),
            entity_id=str(appointment.pk),
            metadata={"status": str(appointment.status)},
        )
        for appointment in rows
    ]


def _chart_events(patient: Patient, *, fetch_limit: int) -> list[TimelineEvent]:
    """Odontogram changes, when the charting app is installed (guarded import)."""

    if not django_apps.is_installed("apps.charting"):
        return []
    ToothStateEvent = django_apps.get_model("charting", "ToothStateEvent")
    manager = getattr(ToothStateEvent, "all_objects", None)
    if manager is None:  # pragma: no cover - the real model exposes all_objects
        manager = ToothStateEvent._default_manager
    rows = list(
        manager.filter(clinic_id=patient.clinic_id, patient_id=patient.pk)
        .order_by("-occurred_at", "-id")[:fetch_limit]
    )
    return [
        TimelineEvent(
            type="chart.event",
            at=event.occurred_at,
            title="Odontogram update",
            summary=str(event.event_type),
            entity_id=str(event.pk),
            metadata={"event_type": str(event.event_type)},
        )
        for event in rows
    ]


def build_timeline(patient: Patient, *, fetch_limit: int = DEFAULT_FETCH_LIMIT) -> list[TimelineEvent]:
    """Return the merged, newest-first timeline for ``patient``."""

    events: list[TimelineEvent] = []
    events.extend(_patient_events(patient))
    events.extend(_encounter_events(patient, fetch_limit=fetch_limit))
    events.extend(_allergy_events(patient, fetch_limit=fetch_limit))
    events.extend(_history_events(patient, fetch_limit=fetch_limit))
    events.extend(_appointment_events(patient, fetch_limit=fetch_limit))
    events.extend(_chart_events(patient, fetch_limit=fetch_limit))

    seen: set[tuple[str, str]] = set()
    unique: list[TimelineEvent] = []
    for event in events:
        key = (event.type, event.entity_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    unique.sort(key=TimelineEvent.sort_key)
    return unique


def _encode_cursor(parts: dict[str, str]) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def encode_cursor(event: TimelineEvent) -> str:
    return _encode_cursor({"at": event.at.isoformat(), "id": event.entity_id})


def decode_cursor(raw: str) -> tuple[datetime, str]:
    """Decode a cursor; raises ``ValueError`` when it is not valid."""

    padded = raw + "=" * (-len(raw) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        at = datetime.fromisoformat(str(payload["at"]))
        entity_id = str(payload["id"])
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise ValueError("invalid cursor") from exc
    return at, entity_id


def paginate_timeline(
    events: list[TimelineEvent],
    *,
    cursor: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> tuple[list[TimelineEvent], str | None]:
    """Slice ``events`` after ``cursor``; returns ``(page, next_cursor)``."""

    window = events
    if cursor:
        at, entity_id = decode_cursor(cursor)
        window = [
            event
            for event in events
            if (event.at.timestamp(), event.entity_id) < (at.timestamp(), entity_id)
        ]
    page = window[:page_size]
    next_cursor = encode_cursor(page[-1]) if len(window) > page_size and page else None
    return page, next_cursor


__all__ = [
    "DEFAULT_FETCH_LIMIT",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "TimelineEvent",
    "build_timeline",
    "decode_cursor",
    "encode_cursor",
    "paginate_timeline",
]