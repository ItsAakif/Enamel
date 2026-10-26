"""``book_appointment`` tool (T124).

The patient is never supplied by the model: it comes from ``ctx.patient``, which
the server resolves from the call after identity confirmation. The idempotency
key is derived from the call, the appointment type, the start time and the
patient, so a repeated identical booking returns the existing appointment
instead of creating a second row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from apps.scheduling.models import Appointment
from apps.scheduling.services.appointments import create_appointment

from ..providers.base import canonical_json, sha256_hex
from .base import Tool, ToolContext, ToolRejected
from .domain import default_location, default_provider, resolve_appointment_type


class BookArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appointment_type: str
    start_at: datetime
    reason: str = ""


def booking_key(ctx: ToolContext, args: BookArgs) -> str:
    payload = {
        "tool": "book_appointment",
        "call": str(getattr(ctx.call, "id", "")),
        "type": args.appointment_type,
        "start": args.start_at.isoformat(),
        "patient": str(getattr(ctx.patient, "id", "")),
    }
    return f"ai-book-{sha256_hex(canonical_json(payload))[:40]}"


def _handler(ctx: ToolContext, args: BookArgs) -> dict[str, Any]:
    if ctx.patient is None:
        raise ToolRejected("The caller's identity must be confirmed first.", code="identity_required")

    clinic = ctx.clinic
    appointment_type = resolve_appointment_type(clinic, args.appointment_type)
    provider = default_provider(clinic)
    location = default_location(clinic)
    key = booking_key(ctx, args)

    existing = (
        Appointment.all_objects.filter(clinic=clinic, idempotency_key=key)
        .order_by("created_at")
        .first()
    )
    if existing is not None:
        return {
            "appointment_id": str(existing.id),
            "start_at": existing.start_at.isoformat(),
            "end_at": existing.end_at.isoformat(),
            "status": existing.status,
            "provider": provider.display_name,
            "duplicate": True,
        }

    appointment = create_appointment(
        clinic,
        patient=ctx.patient,
        provider=provider,
        appointment_type=appointment_type,
        location=location,
        start_at=args.start_at,
        source=Appointment.Source.AI,
        reason=args.reason,
        idempotency_key=key,
        actor=ctx.actor,
        request=ctx.request,
    )
    return {
        "appointment_id": str(appointment.id),
        "start_at": appointment.start_at.isoformat(),
        "end_at": appointment.end_at.isoformat(),
        "status": appointment.status,
        "provider": provider.display_name,
        "duplicate": False,
    }


TOOL = Tool(
    name="book_appointment",
    description="Book an appointment for the confirmed caller.",
    args_model=BookArgs,
    handler=_handler,
    required_perms=("scheduling.manage",),
    consequential=True,
    requires_patient=True,
)


__all__ = ["TOOL", "BookArgs", "booking_key"]
