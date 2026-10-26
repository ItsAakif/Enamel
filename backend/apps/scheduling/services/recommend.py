"""Explainable waitlist scoring and recommendations (T116).

Scoring is deterministic: every candidate is scored by five weighted components
and the breakdown is persisted with the offer. The total is exactly the sum of
the rounded contributions, so a reviewer can re-add them by hand.

Components (weight):

* ``type_match`` (0.30) - the entry's appointment type equals the gap's;
* ``provider_preference`` (0.20) - the preferred provider is the gap provider
  (1.0), no preference was set (0.5) or a different provider was preferred (0);
* ``time_window_fit`` (0.20) - the fraction of the gap that falls inside the
  patient's availability window;
* ``urgency`` (0.15) - ``(urgency - 1) / 4``;
* ``waiting_time`` (0.15) - days waiting, capped at 30.

Every component also produces a human-readable reason. Recommendations respect
duration + buffers, lead time, operatory availability and provider qualification
(``appointment_type.eligible_roles``).
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timedelta
from typing import Any

from django.utils import timezone

from apps.clinics.models import Provider

from ..models import AppointmentType, WaitlistEntry
from .availability import booking_policy
from .waitlist import detect_gaps

COMPONENT_WEIGHTS: dict[str, float] = {
    "type_match": 0.30,
    "provider_preference": 0.20,
    "time_window_fit": 0.20,
    "urgency": 0.15,
    "waiting_time": 0.15,
}
COMPONENT_LABELS: dict[str, str] = {
    "type_match": "Appointment type match",
    "provider_preference": "Provider preference",
    "time_window_fit": "Availability window fit",
    "urgency": "Urgency",
    "waiting_time": "Time spent waiting",
}
WAITING_CAP_DAYS = 30.0
RECOMMENDATION_LIMIT = 3


def _waiting_days(entry: Any, moment: datetime) -> float:
    created = getattr(entry, "created_at", None)
    if created is None:
        return 0.0
    return max(0.0, (moment - created).total_seconds() / 86400.0)


def _reasons(entry: Any, values: dict[str, float], days: float) -> list[str]:
    reasons: list[str] = []
    if values["type_match"] >= 1.0:
        reasons.append("Matches the requested appointment type")
    if values["provider_preference"] >= 1.0:
        reasons.append("Preferred provider is available")
    elif getattr(entry, "provider_pref_id", None) is None:
        reasons.append("No provider preference set")
    if values["time_window_fit"] > 0:
        reasons.append(
            f"Fits {round(values['time_window_fit'] * 100)}% of the patient's window"
        )
    reasons.append(f"Urgency {int(entry.urgency)}/5")
    reasons.append(f"Waiting {round(days)} day(s)")
    return reasons


def score_entry(entry: Any, gap: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Return ``{total, components, reasons}`` for one candidate and one gap."""

    moment = now or timezone.now()
    gap_start: datetime = gap["start"]
    gap_end: datetime = gap["end"]

    gap_type = gap.get("appointment_type_id")
    type_value = 1.0 if gap_type in (None, str(entry.appointment_type_id)) else 0.0

    pref_id = getattr(entry, "provider_pref_id", None)
    gap_provider = gap.get("provider_id")
    if pref_id is None:
        pref_value = 0.5
    elif gap_provider is not None and str(pref_id) == str(gap_provider):
        pref_value = 1.0
    else:
        pref_value = 0.0

    gap_seconds = (gap_end - gap_start).total_seconds()
    overlap = min(entry.window_end, gap_end) - max(entry.window_start, gap_start)
    fit_value = (
        0.0
        if gap_seconds <= 0
        else max(0.0, min(1.0, overlap.total_seconds() / gap_seconds))
    )

    urgency_value = (int(entry.urgency) - 1) / 4.0
    days = _waiting_days(entry, moment)
    wait_value = min(1.0, days / WAITING_CAP_DAYS)

    values = {
        "type_match": type_value,
        "provider_preference": pref_value,
        "time_window_fit": fit_value,
        "urgency": urgency_value,
        "waiting_time": wait_value,
    }
    components = [
        {
            "key": key,
            "label": COMPONENT_LABELS[key],
            "weight": COMPONENT_WEIGHTS[key],
            "value": round(values[key], 4),
            "contribution": round(COMPONENT_WEIGHTS[key] * values[key], 4),
        }
        for key in COMPONENT_WEIGHTS
    ]
    total = round(sum(component["contribution"] for component in components), 4)
    return {"total": total, "components": components, "reasons": _reasons(entry, values, days)}


def _feasible(
    clinic: Any,
    gap: dict[str, Any],
    appointment_type: AppointmentType,
    moment: datetime,
) -> bool:
    """Duration + buffers, lead time, operatory and qualification feasibility."""

    duration_min = appointment_type.duration_min
    buffers = appointment_type.buffer_before_min + appointment_type.buffer_after_min
    gap_minutes = (gap["end"] - gap["start"]).total_seconds() / 60.0
    if gap_minutes < duration_min + buffers:
        return False

    lead_hours = booking_policy(clinic)["lead_time_hours"]
    if gap["start"] < moment + timedelta(hours=lead_hours):
        return False

    if appointment_type.requires_operatory and not gap.get("operatory_id"):
        return False

    eligible = list(appointment_type.eligible_roles or [])
    provider_id = gap.get("provider_id")
    if eligible and provider_id:
        provider = (
            Provider.all_objects.filter(pk=provider_id)
            .select_related("membership")
            .first()
        )
        if provider is not None and provider.membership.role not in eligible:
            return False
    return True


def rank_entries(
    clinic: Any,
    gap: dict[str, Any],
    *,
    now: datetime | None = None,
    appointment_type: AppointmentType | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Rank active waitlist entries against one gap (best first)."""

    moment = now or timezone.now()
    queryset = WaitlistEntry.all_objects.filter(
        clinic=clinic,
        status=WaitlistEntry.Status.ACTIVE,
        window_start__lt=gap["end"],
        window_end__gt=gap["start"],
    )
    if appointment_type is not None:
        queryset = queryset.filter(appointment_type_id=appointment_type.pk)
    if gap.get("provider_id"):
        from django.db.models import Q

        queryset = queryset.filter(
            Q(provider_pref_id__isnull=True) | Q(provider_pref_id=gap["provider_id"])
        )

    ranked: list[dict[str, Any]] = []
    for entry in queryset:
        score = score_entry(entry, gap, now=moment)
        ranked.append(
            {
                "entry_id": str(entry.id),
                "patient_id": str(entry.patient_id),
                "appointment_type_id": str(entry.appointment_type_id),
                "provider_pref_id": (
                    str(entry.provider_pref_id) if entry.provider_pref_id else None
                ),
                "urgency": entry.urgency,
                "window_start": entry.window_start,
                "window_end": entry.window_end,
                **score,
            }
        )
    ranked.sort(key=lambda item: (-item["total"], item["entry_id"]))
    return ranked if limit is None else ranked[:limit]


def recommendations(
    clinic: Any,
    on_date: date_type,
    appointment_type: AppointmentType,
    *,
    now: datetime | None = None,
    limit: int = RECOMMENDATION_LIMIT,
) -> dict[str, Any]:
    """Top-N explainable candidates for one date and appointment type."""

    moment = now or timezone.now()
    gaps = detect_gaps(clinic, on_date, on_date, appointment_type=appointment_type, now=moment)

    results: list[dict[str, Any]] = []
    for gap in gaps:
        if not _feasible(clinic, gap, appointment_type, moment):
            continue
        for ranked in rank_entries(
            clinic, gap, now=moment, appointment_type=appointment_type
        ):
            results.append(
                {
                    "entry_id": ranked["entry_id"],
                    "patient_id": ranked["patient_id"],
                    "provider_id": gap.get("provider_id"),
                    "operatory_id": gap.get("operatory_id"),
                    "start_at": gap["start"],
                    "end_at": gap["end"],
                    "gap_reason": gap["reason"],
                    "total": ranked["total"],
                    "components": ranked["components"],
                    "reasons": ranked["reasons"],
                }
            )

    results.sort(key=lambda item: (-item["total"], item["entry_id"], item["start_at"]))
    return {
        "date": on_date.isoformat(),
        "appointment_type_id": str(appointment_type.id),
        "recommendations": results[:limit],
        "definitions": {
            "weights": COMPONENT_WEIGHTS,
            "waiting_cap_days": WAITING_CAP_DAYS,
            "total": "sum of weight * value for each component",
            "feasibility": (
                "duration + buffers, lead time, operatory availability and "
                "provider qualification (eligible_roles)"
            ),
        },
    }


__all__ = [
    "COMPONENT_LABELS",
    "COMPONENT_WEIGHTS",
    "RECOMMENDATION_LIMIT",
    "WAITING_CAP_DAYS",
    "rank_entries",
    "recommendations",
    "score_entry",
]
