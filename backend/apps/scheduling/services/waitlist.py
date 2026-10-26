"""Waitlist registry and gap detection (T116).

A waitlist entry is a patient wanting a slot of one appointment type inside a
time window, with an urgency (1-5) and an optional provider preference.

Gaps come from two sources:

* **cancellations** - a cancelled appointment whose slot has not been refilled
  by another active appointment (checked with the shared conflict lookup);
* **availability** - bookable slots from the shared slot engine, when a caller
  asks for a specific appointment type.

Both are returned as plain dicts so the recommender, the API and the scenario
runner all consume one shape.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.clinics.models import Provider
from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.tenancy import with_clinic

from ..models import Appointment, AppointmentType, WaitlistEntry
from ..services.appointments import find_conflict
from ..services.availability import compute_slots, window_bounds
from ..status import STATUS_CANCELLED

EDITABLE_FIELDS = ("window_start", "window_end", "urgency", "provider_pref_id", "notes", "status")


def _validation(field: str, message: str) -> ApiError:
    return ApiError(
        "validation_error",
        "The request could not be processed.",
        status_code=400,
        field_errors={field: [message]},
    )


def snapshot(entry: WaitlistEntry) -> dict[str, Any]:
    return {
        "patient_id": str(entry.patient_id),
        "appointment_type_id": str(entry.appointment_type_id),
        "provider_pref_id": str(entry.provider_pref_id) if entry.provider_pref_id else None,
        "window_start": entry.window_start.isoformat(),
        "window_end": entry.window_end.isoformat(),
        "urgency": entry.urgency,
        "status": entry.status,
    }


def create_entry(
    clinic: Any,
    *,
    patient: Any,
    appointment_type: Any,
    window_start: datetime,
    window_end: datetime,
    urgency: int = 3,
    provider_pref: Provider | None = None,
    consent_snapshot: dict[str, Any] | None = None,
    notes: str = "",
    actor: Any = None,
    request: Any = None,
) -> WaitlistEntry:
    """Create a waitlist entry after validating the window and urgency."""

    if window_start is None or window_end is None:
        raise _validation("window_start", "a window is required")
    if timezone.is_naive(window_start) or timezone.is_naive(window_end):
        raise _validation("window_start", "must include a timezone offset")
    if window_end <= window_start:
        raise _validation("window_end", "must be after window_start")
    if urgency < 1 or urgency > 5:
        raise _validation("urgency", "must be between 1 and 5")
    for name, obj in (
        ("patient", patient),
        ("appointment_type", appointment_type),
        ("provider_pref", provider_pref),
    ):
        if obj is not None and getattr(obj, "clinic_id", None) != clinic.id:
            raise _validation(name, "not found in this clinic")

    with transaction.atomic():
        with with_clinic(clinic):
            entry = WaitlistEntry.all_objects.create(
                clinic=clinic,
                patient=patient,
                appointment_type=appointment_type,
                provider_pref=provider_pref,
                window_start=window_start,
                window_end=window_end,
                urgency=urgency,
                status=WaitlistEntry.Status.ACTIVE,
                consent_snapshot=consent_snapshot or {},
                notes=notes or "",
            )
            record_audit(
                "waitlist.entry_created",
                entity=entry,
                after=snapshot(entry),
                actor=actor,
                request=request,
            )
    return entry


def update_entry(
    entry: WaitlistEntry,
    *,
    fields: dict[str, Any],
    actor: Any = None,
    request: Any = None,
) -> WaitlistEntry:
    """Apply ``fields`` to ``entry`` and audit the change."""

    changes = {name: value for name, value in fields.items() if name in EDITABLE_FIELDS}
    if not changes:
        return entry
    if "urgency" in changes and (changes["urgency"] < 1 or changes["urgency"] > 5):
        raise _validation("urgency", "must be between 1 and 5")

    clinic = entry.clinic
    with transaction.atomic():
        with with_clinic(clinic):
            before = snapshot(entry)
            for name, value in changes.items():
                setattr(entry, name, value)
            if entry.window_end <= entry.window_start:
                raise _validation("window_end", "must be after window_start")
            entry.save(update_fields=[*changes, "updated_at"])
            record_audit(
                "waitlist.entry_updated",
                entity=entry,
                before=before,
                after=snapshot(entry),
                actor=actor,
                request=request,
            )
    return entry


def cancel_entry(
    entry: WaitlistEntry, *, actor: Any = None, request: Any = None
) -> WaitlistEntry:
    """Cancel an entry that has no live offer."""

    if entry.status == WaitlistEntry.Status.CLAIMED:
        raise ApiError(
            "conflict",
            "This entry has already been claimed.",
            status_code=409,
        )
    return update_entry(
        entry,
        fields={"status": WaitlistEntry.Status.CANCELLED},
        actor=actor,
        request=request,
    )


def detect_gaps(
    clinic: Any,
    from_date: date_type,
    to_date: date_type,
    *,
    appointment_type: AppointmentType | None = None,
    provider: Provider | None = None,
    location: Any = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return cancellation and (optionally) availability gaps in the window."""

    moment = now or timezone.now()
    window_start, window_end = window_bounds(clinic, from_date, to_date)
    gaps: list[dict[str, Any]] = []

    cancelled = Appointment.all_objects.filter(
        clinic=clinic,
        status=STATUS_CANCELLED,
        start_at__gte=window_start,
        start_at__lt=window_end,
    )
    if provider is not None:
        cancelled = cancelled.filter(provider_id=provider.pk)
    for appointment in cancelled:
        conflict = find_conflict(
            clinic,
            provider_id=appointment.provider_id,
            operatory_id=appointment.operatory_id,
            start_at=appointment.start_at,
            end_at=appointment.end_at,
        )
        if conflict is not None:
            continue
        gaps.append(
            {
                "start": appointment.start_at,
                "end": appointment.end_at,
                "provider_id": str(appointment.provider_id),
                "operatory_id": (
                    str(appointment.operatory_id) if appointment.operatory_id else None
                ),
                "location_id": str(appointment.location_id),
                "appointment_type_id": str(appointment.appointment_type_id),
                "reason": "cancellation",
            }
        )

    if appointment_type is not None:
        result = compute_slots(
            clinic,
            appointment_type,
            from_date,
            to_date,
            provider=provider,
            location=location,
            now=moment,
        )
        for slot in result["slots"]:
            gaps.append(
                {
                    "start": slot["start"],
                    "end": slot["end"],
                    "provider_id": str(slot["provider"].pk) if slot["provider"] else None,
                    "operatory_id": (
                        str(slot["operatory"].pk) if slot["operatory"] else None
                    ),
                    "location_id": str(slot["location"].pk) if slot["location"] else None,
                    "appointment_type_id": str(appointment_type.pk),
                    "reason": "availability",
                }
            )

    gaps.sort(key=lambda item: (item["start"], item["provider_id"] or ""))
    return gaps


__all__ = [
    "EDITABLE_FIELDS",
    "cancel_entry",
    "create_entry",
    "detect_gaps",
    "snapshot",
    "update_entry",
]
