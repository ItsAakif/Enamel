"""Operational scheduling metrics (T110).

Definitions (also returned in ``definition`` so the numbers are self-describing):

* **Window**: appointments whose ``start_at`` falls inside
  ``[from_date 00:00 local, to_date+1 00:00 local)`` in the clinic zone.
* **By status**: every appointment in the window, counted by current status.
* **Completion / cancellation / no-show rate**: count / total appointments in the
  window (0 when the window is empty).
* **Booked minutes**: sum of ``end_at - start_at`` for appointments in the window
  with a *consuming* status (``scheduled``, ``confirmed``, ``checked_in``,
  ``in_progress``, ``completed``). Cancelled and no-show rows contribute nothing.
* **Available minutes**: the expanded availability denominator from
  :func:`apps.scheduling.services.availability.available_minutes` -- every working
  interval per ``(location, provider lane)`` in the window, minus break/block
  rules and schedule exceptions, measured as real (UTC elapsed) minutes.
* **Utilisation**: ``booked_minutes / available_minutes`` (0 when the
  denominator is 0). Because the denominator counts each provider lane, a clinic
  with N bookable providers has N times the wall-clock hours, so utilisation is
  per-provider-slot rather than per-wall-clock.
* **New vs returning**: a patient is *new* when their first ever appointment in
  the clinic starts inside the window, otherwise *returning*.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Any

from django.db.models import Min

from ..models import Appointment
from ..status import (
    ALL_STATUSES,
    STATUS_CANCELLED,
    STATUS_CHECKED_IN,
    STATUS_COMPLETED,
    STATUS_CONFIRMED,
    STATUS_IN_PROGRESS,
    STATUS_NO_SHOW,
    STATUS_SCHEDULED,
)
from .availability import available_minutes, clinic_timezone, window_bounds

#: Statuses that consume booked time (a cancelled/no-show row does not).
BOOKED_STATUSES = (
    STATUS_SCHEDULED,
    STATUS_CONFIRMED,
    STATUS_CHECKED_IN,
    STATUS_IN_PROGRESS,
    STATUS_COMPLETED,
)


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def metrics(
    clinic: Any,
    from_date: date_type,
    to_date: date_type,
    *,
    location: Any = None,
) -> dict[str, Any]:
    """Return the operational metrics for one clinic and window."""

    window_start, window_end = window_bounds(clinic, from_date, to_date)
    queryset = Appointment.all_objects.filter(
        clinic=clinic,
        start_at__gte=window_start,
        start_at__lt=window_end,
    )
    if location is not None:
        queryset = queryset.filter(location_id=getattr(location, "id", location))

    rows = list(queryset.only("patient_id", "status", "start_at", "end_at"))

    by_status = {status: 0 for status in ALL_STATUSES}
    booked_minutes = 0
    patients: set[Any] = set()
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        patients.add(row.patient_id)
        if row.status in BOOKED_STATUSES:
            booked_minutes += int((row.end_at - row.start_at).total_seconds() // 60)

    total = len(rows)
    completed = by_status[STATUS_COMPLETED]
    cancelled = by_status[STATUS_CANCELLED]
    no_show = by_status[STATUS_NO_SHOW]

    available = available_minutes(clinic, from_date, to_date, location=location)

    returning = 0
    if patients:
        earliest = (
            Appointment.all_objects.filter(clinic=clinic, patient_id__in=list(patients))
            .values("patient_id")
            .annotate(first_start=Min("start_at"))
        )
        first_by_patient = {row["patient_id"]: row["first_start"] for row in earliest}
        for patient_id in patients:
            first_start = first_by_patient.get(patient_id)
            if first_start is not None and first_start < window_start:
                returning += 1
    new_patients = len(patients) - returning

    return {
        "window": {
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "timezone": str(clinic_timezone(clinic)),
            "location_id": str(getattr(location, "id", location)) if location is not None else None,
            "start_at": window_start,
            "end_at": window_end,
        },
        "appointments": {"total": total, "by_status": by_status},
        "completed": completed,
        "cancelled": cancelled,
        "no_show": no_show,
        "completion_rate": _rate(completed, total),
        "cancellation_rate": _rate(cancelled, total),
        "no_show_rate": _rate(no_show, total),
        "booked_minutes": booked_minutes,
        "available_minutes": available,
        "utilisation": _rate(booked_minutes, available),
        "patients": {
            "distinct": len(patients),
            "new": new_patients,
            "returning": returning,
        },
        "definition": {
            "booked_statuses": list(BOOKED_STATUSES),
            "available_denominator": (
                "expanded working minutes per (location, provider lane) minus "
                "breaks/blocks and schedule exceptions, in real UTC minutes"
            ),
            "utilisation": "booked_minutes / available_minutes",
            "new_patient": "first ever appointment in the clinic starts inside the window",
        },
    }


__all__ = ["BOOKED_STATUSES", "metrics"]
