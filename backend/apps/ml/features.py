"""Appointment -> booking-time feature extraction for inference (T131).

This is the serving-side mirror of ``ml.no_show.dataset``: it reads persisted
data and produces the same feature vector the model was trained on, using only
information known at the booking instant.

Missing features are reported, never invented. A feature is "missing" when the
clinic has no persisted source for it (e.g. there is no insurance model, so
``insurance_self_pay`` must come from the request) or when the underlying
record is absent (e.g. a patient without a date of birth has no age band). The
view turns a non-empty missing list into a 422 that names every gap.

Cold starts are *not* missing: a patient with no earlier terminal appointment
gets prior counts of zero plus ``is_new_patient=True``, exactly as the spec's
edge case requires.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from django.utils import timezone

from apps.messaging.models import Consent
from apps.scheduling.models import Appointment
from apps.scheduling.status import STATUS_CANCELLED, STATUS_COMPLETED, STATUS_NO_SHOW
from ml.no_show.config import CATEGORICAL_FEATURES, FEATURE_NAMES
from ml.no_show.features import missing_features

#: Statuses that end an appointment. Only these count towards patient history.
TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_CANCELLED, STATUS_NO_SHOW)

_AGE_BANDS = CATEGORICAL_FEATURES["patient_age_band"]


def age_band(date_of_birth: date, on_date: date) -> str:
    """Bucket a date of birth into the documented age band."""

    years = on_date.year - date_of_birth.year - (
        (on_date.month, on_date.day) < (date_of_birth.month, date_of_birth.day)
    )
    if years < 18:
        return "0-17"
    if years < 35:
        return "18-34"
    if years < 50:
        return "35-49"
    if years < 65:
        return "50-64"
    return "65+"


def _history(appointment: Appointment) -> dict[str, Any]:
    """Booking-time patient history over strictly earlier appointments."""

    earlier = Appointment.all_objects.filter(
        clinic_id=appointment.clinic_id,
        patient_id=appointment.patient_id,
        status__in=TERMINAL_STATUSES,
        start_at__lt=appointment.start_at,
    ).values_list("status", flat=True)
    no_shows = 0
    cancellations = 0
    visits = 0
    for status in earlier:
        if status == STATUS_NO_SHOW:
            no_shows += 1
        elif status == STATUS_CANCELLED:
            cancellations += 1
        elif status == STATUS_COMPLETED:
            visits += 1
    total = no_shows + cancellations + visits
    return {
        "prior_no_show_count": no_shows,
        "prior_cancellations": cancellations,
        "prior_visits": visits,
        "prior_no_show_rate": round(no_shows / max(total, 1), 6),
        "is_new_patient": total == 0,
    }


def _reminder_consent(appointment: Appointment) -> bool:
    return Consent.all_objects.filter(
        clinic_id=appointment.clinic_id,
        patient_id=appointment.patient_id,
        purpose=Consent.Purpose.APPOINTMENT_REMINDERS,
        status=Consent.Status.GRANTED,
    ).exists()


def extract_features(
    appointment: Appointment,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Return ``(features, missing)`` for ``appointment``.

    ``overrides`` carries feature values the clinic has not persisted (today:
    ``insurance_self_pay`` and ``distance_band``). An override for a feature
    that *is* derivable replaces the derived value, which lets a caller pin a
    known value without the API guessing.
    """

    clinic = appointment.clinic
    tz = timezone.get_current_timezone()
    if clinic is not None and getattr(clinic, "timezone", ""):
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(clinic.timezone)
        except Exception:  # pragma: no cover - clinics validate tz on write
            tz = timezone.get_current_timezone()

    start_local = timezone.localtime(appointment.start_at, tz)
    booked_at = appointment.created_at or appointment.start_at
    lead_time_days = max(
        0.0, round((appointment.start_at - booked_at).total_seconds() / 86400.0, 2)
    )
    provider_tenure_days = max(
        0.0,
        round((booked_at - appointment.provider.created_at).total_seconds() / 86400.0, 2),
    )

    features: dict[str, Any] = {
        "lead_time_days": lead_time_days,
        "hour_of_day": start_local.hour,
        "weekday": start_local.weekday(),
        "appointment_type": appointment.appointment_type.code,
        "provider_tenure_days": provider_tenure_days,
        "source": appointment.source,
        "reminder_consent": _reminder_consent(appointment),
        **_history(appointment),
    }

    patient = appointment.patient
    if patient.date_of_birth is not None:
        features["patient_age_band"] = age_band(
            patient.date_of_birth, start_local.date()
        )

    if overrides:
        for name in FEATURE_NAMES:
            if name in overrides:
                features[name] = overrides[name]

    missing = missing_features(features)
    ordered = {name: features[name] for name in FEATURE_NAMES if name in features}
    return ordered, missing


__all__ = ["TERMINAL_STATUSES", "age_band", "extract_features"]
