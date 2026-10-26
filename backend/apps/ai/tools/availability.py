"""``check_availability`` tool (T124)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from apps.scheduling.services.availability import compute_slots

from .base import Tool, ToolContext
from .domain import default_provider, parse_date, resolve_appointment_type


class AvailabilityArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appointment_type: str
    date: str
    provider_id: str | None = None


def _handler(ctx: ToolContext, args: AvailabilityArgs) -> dict[str, Any]:
    clinic = ctx.clinic
    appointment_type = resolve_appointment_type(clinic, args.appointment_type)
    day = parse_date(args.date)
    provider = default_provider(clinic, args.provider_id) if args.provider_id else None

    result = compute_slots(clinic, appointment_type, day, day, provider=provider)
    slots = result["slots"]
    offers = [
        {
            "start_at": slot["start"].isoformat(),
            "end_at": slot["end"].isoformat(),
            "provider": getattr(slot.get("provider"), "display_name", ""),
        }
        for slot in slots[:5]
    ]
    return {
        "count": len(slots),
        "slots": offers,
        "message": "" if slots else "No availability on that day; try another date.",
    }


TOOL = Tool(
    name="check_availability",
    description="List bookable appointment slots for a type on a given date.",
    args_model=AvailabilityArgs,
    handler=_handler,
    required_perms=("scheduling.view",),
    consequential=False,
    requires_patient=False,
)


__all__ = ["TOOL", "AvailabilityArgs"]
