"""``cancel_appointment`` tool (T124)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from apps.scheduling.services.appointments import cancel

from .base import Tool, ToolContext
from .domain import resolve_owned_appointment


class CancelArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appointment_id: str
    reason: str = ""


def _handler(ctx: ToolContext, args: CancelArgs) -> dict[str, Any]:
    appointment = resolve_owned_appointment(ctx, args.appointment_id)
    cancelled = cancel(
        appointment,
        reason=args.reason or "cancelled by caller",
        actor=ctx.actor,
        request=ctx.request,
    )
    return {
        "appointment_id": str(cancelled.id),
        "status": cancelled.status,
    }


TOOL = Tool(
    name="cancel_appointment",
    description="Cancel the caller's existing appointment.",
    args_model=CancelArgs,
    handler=_handler,
    required_perms=("scheduling.manage",),
    consequential=True,
    requires_patient=True,
)


__all__ = ["TOOL", "CancelArgs"]
