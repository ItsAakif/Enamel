"""``reschedule_appointment`` tool (T124)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from apps.scheduling.services.appointments import reschedule

from .base import Tool, ToolContext
from .domain import resolve_owned_appointment


class RescheduleArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appointment_id: str
    start_at: datetime
    reason: str = ""


def _handler(ctx: ToolContext, args: RescheduleArgs) -> dict[str, Any]:
    appointment = resolve_owned_appointment(ctx, args.appointment_id)
    new = reschedule(
        appointment,
        args.start_at,
        reason=args.reason,
        actor=ctx.actor,
        request=ctx.request,
    )
    return {
        "appointment_id": str(new.id),
        "rescheduled_from": str(appointment.id),
        "start_at": new.start_at.isoformat(),
        "end_at": new.end_at.isoformat(),
        "status": new.status,
    }


TOOL = Tool(
    name="reschedule_appointment",
    description="Move the caller's existing appointment to a new time.",
    args_model=RescheduleArgs,
    handler=_handler,
    required_perms=("scheduling.manage",),
    consequential=True,
    requires_patient=True,
)


__all__ = ["TOOL", "RescheduleArgs"]
