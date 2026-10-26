"""API resource projections and query parsing for scheduling (T110).

Views stay thin: the shapes returned to clients for availability slots and the
window summary are built here, and the ``/availability/`` query parameters are
parsed here so the same validation backs the endpoint and the tests.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Any

from django.utils.dateparse import parse_date

from apps.core.errors import ApiError


def _validation(field: str, message: str) -> ApiError:
    return ApiError(
        "validation_error",
        "The request could not be processed.",
        status_code=400,
        field_errors={field: [message]},
    )


def parse_date_param(value: str | None, field: str) -> date_type:
    """Parse an ISO date query parameter into a ``date`` or raise a 400."""

    raw = (value or "").strip()
    if not raw:
        raise _validation(field, "this parameter is required")
    parsed = parse_date(raw)
    if parsed is None:
        raise _validation(field, "enter a valid ISO-8601 date")
    return parsed


def slot_resource(slot: dict[str, Any]) -> dict[str, Any]:
    """Serialize one availability slot (ids only, never patient data)."""

    provider = slot.get("provider")
    operatory = slot.get("operatory")
    location = slot.get("location")
    return {
        "start": slot["start"],
        "end": slot["end"],
        "provider": str(provider.pk) if provider is not None else None,
        "provider_name": getattr(provider, "display_name", None),
        "operatory": str(operatory.pk) if operatory is not None else None,
        "operatory_name": getattr(operatory, "name", None),
        "location": str(location.pk) if location is not None else None,
        "reason": slot.get("reason", "available"),
    }


def availability_resource(result: dict[str, Any]) -> dict[str, Any]:
    """Serialize a :func:`compute_slots` result."""

    window = result["window"]
    return {
        "window": {
            "from": window["from"],
            "to": window["to"],
            "timezone": window["timezone"],
            "step_min": window["step_min"],
            "lead_time_hours": window["lead_time_hours"],
            "max_days": window["max_days"],
            "days": window["days"],
            "slot_count": window["slot_count"],
            "reasons": window["reasons"],
            "reason": window["reason"],
        },
        "slots": [slot_resource(slot) for slot in result["slots"]],
    }


__all__ = [
    "availability_resource",
    "parse_date_param",
    "slot_resource",
]
