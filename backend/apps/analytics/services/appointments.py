"""Appointment, utilisation and patient-mix metrics (T133).

All counts use ``start_at`` inside the resolved half-open window, matching the
scheduling metrics contract. The utilisation denominator is imported from the
scheduling service so the two screens can never disagree about what "available"
means.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Any

from django.db.models import Min

from apps.scheduling.models import Appointment
from apps.scheduling.services.availability import available_minutes
from apps.scheduling.services.metrics import BOOKED_STATUSES
from apps.scheduling.status import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_NO_SHOW,
)

from ..metrics import day_series_bounds, resolve_range
from ..registry import payload, register

DOMAIN = "appointments"


def _queryset(clinic: Any, from_date: date_type, to_date: date_type, location: Any):
    start, end = resolve_range(clinic, from_date, to_date)
    queryset = Appointment.all_objects.filter(
        clinic=clinic, start_at__gte=start, start_at__lt=end
    )
    if location is not None:
        queryset = queryset.filter(location_id=getattr(location, "id", location))
    return queryset


def _count(clinic, from_date, to_date, location, status: str) -> int:
    return _queryset(clinic, from_date, to_date, location).filter(status=status).count()


def total(clinic, from_date, to_date, location=None):
    count = _queryset(clinic, from_date, to_date, location).count()
    return payload(
        "appointments.total", value=count, numerator_value=count, denominator_value=1
    )


def completed(clinic, from_date, to_date, location=None):
    count = _count(clinic, from_date, to_date, location, STATUS_COMPLETED)
    return payload(
        "appointments.completed", value=count, numerator_value=count, denominator_value=1
    )


def cancelled(clinic, from_date, to_date, location=None):
    count = _count(clinic, from_date, to_date, location, STATUS_CANCELLED)
    return payload(
        "appointments.cancelled", value=count, numerator_value=count, denominator_value=1
    )


def no_show(clinic, from_date, to_date, location=None):
    count = _count(clinic, from_date, to_date, location, STATUS_NO_SHOW)
    return payload(
        "appointments.no_show", value=count, numerator_value=count, denominator_value=1
    )


def _rate(key: str, numerator: int, denominator: int):
    value = round(numerator / denominator, 4) if denominator > 0 else 0.0
    return payload(
        key,
        value=value,
        numerator_value=numerator,
        denominator_value=denominator,
    )


def completion_rate(clinic, from_date, to_date, location=None):
    return _rate(
        "appointments.completion_rate",
        _count(clinic, from_date, to_date, location, STATUS_COMPLETED),
        _queryset(clinic, from_date, to_date, location).count(),
    )


def cancellation_rate(clinic, from_date, to_date, location=None):
    return _rate(
        "appointments.cancellation_rate",
        _count(clinic, from_date, to_date, location, STATUS_CANCELLED),
        _queryset(clinic, from_date, to_date, location).count(),
    )


def no_show_rate(clinic, from_date, to_date, location=None):
    return _rate(
        "appointments.no_show_rate",
        _count(clinic, from_date, to_date, location, STATUS_NO_SHOW),
        _queryset(clinic, from_date, to_date, location).count(),
    )


def booked_minutes(clinic, from_date, to_date, location=None) -> int:
    rows = _queryset(clinic, from_date, to_date, location).filter(
        status__in=BOOKED_STATUSES
    )
    total_minutes = 0
    for row in rows.only("start_at", "end_at").iterator():
        total_minutes += int((row.end_at - row.start_at).total_seconds() // 60)
    return total_minutes


def booked_minutes_metric(clinic, from_date, to_date, location=None):
    minutes = booked_minutes(clinic, from_date, to_date, location)
    return payload(
        "appointments.booked_minutes",
        value=minutes,
        numerator_value=minutes,
        denominator_value=1,
    )


def available_minutes_metric(clinic, from_date, to_date, location=None):
    minutes = int(available_minutes(clinic, from_date, to_date, location=location))
    return payload(
        "appointments.available_minutes",
        value=minutes,
        numerator_value=minutes,
        denominator_value=1,
    )


def utilisation(clinic, from_date, to_date, location=None):
    booked = booked_minutes(clinic, from_date, to_date, location)
    available = int(available_minutes(clinic, from_date, to_date, location=location))
    value = round(booked / available, 4) if available > 0 else 0.0
    return payload(
        "appointments.utilisation",
        value=value,
        numerator_value=booked,
        denominator_value=available,
    )


def patient_mix(clinic, from_date, to_date, location=None) -> dict[str, int]:
    """Distinct patients in the window split into new and returning."""

    start, _ = resolve_range(clinic, from_date, to_date)
    patient_ids = set(
        _queryset(clinic, from_date, to_date, location).values_list(
            "patient_id", flat=True
        )
    )
    returning = 0
    if patient_ids:
        earliest = (
            Appointment.all_objects.filter(clinic=clinic, patient_id__in=list(patient_ids))
            .values("patient_id")
            .annotate(first_start=Min("start_at"))
        )
        first_by_patient = {row["patient_id"]: row["first_start"] for row in earliest}
        for patient_id in patient_ids:
            first_start = first_by_patient.get(patient_id)
            if first_start is not None and first_start < start:
                returning += 1
    return {
        "distinct": len(patient_ids),
        "new": len(patient_ids) - returning,
        "returning": returning,
    }


def new_patients(clinic, from_date, to_date, location=None):
    count = patient_mix(clinic, from_date, to_date, location)["new"]
    return payload(
        "patients.new", value=count, numerator_value=count, denominator_value=1
    )


def returning_patients(clinic, from_date, to_date, location=None):
    count = patient_mix(clinic, from_date, to_date, location)["returning"]
    return payload(
        "patients.returning", value=count, numerator_value=count, denominator_value=1
    )


def by_status(clinic, from_date, to_date, location=None) -> dict[str, int]:
    """Current-status breakdown of the window (the funnel source)."""

    rows = _queryset(clinic, from_date, to_date, location).values_list(
        "status", flat=True
    )
    counts: dict[str, int] = {}
    for status in rows:
        counts[status] = counts.get(status, 0) + 1
    return counts


def series(clinic, from_date, to_date, location=None) -> list[dict[str, Any]]:
    """Per-day counts in the clinic timezone (chart data, never constants)."""

    points: list[dict[str, Any]] = []
    for day, start, end in day_series_bounds(clinic, from_date, to_date):
        queryset = Appointment.all_objects.filter(
            clinic=clinic, start_at__gte=start, start_at__lt=end
        )
        if location is not None:
            queryset = queryset.filter(location_id=getattr(location, "id", location))
        rows = list(queryset.values_list("status", flat=True))
        points.append(
            {
                "date": day.isoformat(),
                "total": len(rows),
                "completed": rows.count(STATUS_COMPLETED),
                "cancelled": rows.count(STATUS_CANCELLED),
                "no_show": rows.count(STATUS_NO_SHOW),
            }
        )
    return points


def report(clinic, from_date, to_date, location=None) -> dict[str, Any]:
    """The full appointments payload."""

    from ..metrics import range_payload

    return {
        "range": range_payload(clinic, from_date, to_date, location=location),
        "metrics": [
            total(clinic, from_date, to_date, location),
            completed(clinic, from_date, to_date, location),
            cancelled(clinic, from_date, to_date, location),
            no_show(clinic, from_date, to_date, location),
            completion_rate(clinic, from_date, to_date, location),
            cancellation_rate(clinic, from_date, to_date, location),
            no_show_rate(clinic, from_date, to_date, location),
            booked_minutes_metric(clinic, from_date, to_date, location),
            available_minutes_metric(clinic, from_date, to_date, location),
            utilisation(clinic, from_date, to_date, location),
            new_patients(clinic, from_date, to_date, location),
            returning_patients(clinic, from_date, to_date, location),
        ],
        "by_status": by_status(clinic, from_date, to_date, location),
        "patients": patient_mix(clinic, from_date, to_date, location),
        "series": series(clinic, from_date, to_date, location),
        "definition": {
            "window": "start_at in [from 00:00 local, to+1 00:00 local)",
            "consuming_statuses": list(BOOKED_STATUSES),
        },
    }


def register_metrics() -> None:
    register("appointments.total", total)
    register("appointments.completed", completed)
    register("appointments.cancelled", cancelled)
    register("appointments.no_show", no_show)
    register("appointments.completion_rate", completion_rate)
    register("appointments.cancellation_rate", cancellation_rate)
    register("appointments.no_show_rate", no_show_rate)
    register("appointments.utilisation", utilisation)
    register("appointments.booked_minutes", booked_minutes_metric)
    register("appointments.available_minutes", available_minutes_metric)
    register("patients.new", new_patients)
    register("patients.returning", returning_patients)


__all__ = [
    "available_minutes_metric",
    "booked_minutes",
    "by_status",
    "cancellation_rate",
    "completed",
    "completion_rate",
    "cancelled",
    "new_patients",
    "no_show",
    "no_show_rate",
    "patient_mix",
    "register_metrics",
    "report",
    "returning_patients",
    "series",
    "total",
    "utilisation",
]
