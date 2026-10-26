"""Shared domain lookups for the tools (T124).

Tools never import another app's models directly except through these helpers,
which always filter by the execution clinic. A reference from another clinic
therefore resolves to ``None`` and becomes a ``not_found`` rejection instead of a
cross-tenant read.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from typing import Any

from apps.clinics.models import Location, Provider
from apps.scheduling.models import AppointmentType

from .base import ToolRejected


def as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def resolve_appointment_type(clinic: Any, ref: Any) -> AppointmentType:
    value = str(ref or "").strip()
    if not value:
        raise ToolRejected("An appointment type is required.", code="invalid_arguments")
    queryset = AppointmentType.all_objects.filter(clinic=clinic, is_active=True)
    resolved = as_uuid(value)
    appointment_type = (
        queryset.filter(pk=resolved).first() if resolved else queryset.filter(code=value.lower()).first()
    )
    if appointment_type is None:
        raise ToolRejected("That appointment type is not available.", code="not_found")
    return appointment_type


def default_provider(clinic: Any, provider_id: Any = None) -> Provider:
    if provider_id:
        resolved = as_uuid(provider_id)
        provider = (
            Provider.all_objects.filter(clinic=clinic, pk=resolved).first() if resolved else None
        )
        if provider is None:
            raise ToolRejected("That provider is not available.", code="not_found")
        return provider
    provider = Provider.all_objects.filter(clinic=clinic, is_bookable=True).order_by(
        "display_name", "id"
    ).first()
    if provider is None:
        raise ToolRejected("No bookable provider is configured.", code="no_provider")
    return provider


def default_location(clinic: Any, location_id: Any = None) -> Location:
    if location_id:
        resolved = as_uuid(location_id)
        location = Location.all_objects.filter(clinic=clinic, pk=resolved).first() if resolved else None
        if location is None:
            raise ToolRejected("That location is not available.", code="not_found")
        return location
    location = Location.all_objects.filter(clinic=clinic, is_active=True).order_by("name", "id").first()
    if location is None:
        raise ToolRejected("No active location is configured.", code="no_location")
    return location


def parse_date(value: Any, field: str = "date") -> date_type:
    if isinstance(value, date_type):
        return value
    from django.utils.dateparse import parse_date as django_parse_date

    parsed = django_parse_date(str(value or "").strip())
    if parsed is None:
        raise ToolRejected(
            "That date is not valid.",
            code="invalid_arguments",
            field_errors={field: ["use YYYY-MM-DD"]},
        )
    return parsed


def resolve_owned_appointment(ctx: Any, appointment_id: Any) -> Any:
    """Return the caller's appointment, or reject with an indistinguishable 404.

    A foreign clinic or a different patient resolves to the same ``not_found``,
    so an appointment id can never be probed across tenants or patients.
    """

    from apps.scheduling.models import Appointment

    resolved = as_uuid(appointment_id)
    appointment = (
        Appointment.all_objects.filter(clinic=ctx.clinic, pk=resolved).first() if resolved else None
    )
    if appointment is None or ctx.patient is None or appointment.patient_id != ctx.patient.id:
        raise ToolRejected("That appointment was not found.", code="not_found")
    return appointment


__all__ = [
    "as_uuid",
    "default_location",
    "default_provider",
    "parse_date",
    "resolve_appointment_type",
    "resolve_owned_appointment",
]
