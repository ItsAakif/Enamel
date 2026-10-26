"""``escalate_to_human`` tool (T124).

Creates a durable staff task (``core.JobRequest``) and ends the AI session. It
is deliberately *not* consequential: a caller asking for a human must always be
honoured without a confirmation step.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from ..services.escalation import escalate_call
from .base import Tool, ToolContext


class EscalateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: str = "caller_request"


def _handler(ctx: ToolContext, args: EscalateArgs) -> dict[str, Any]:
    job = escalate_call(
        ctx.call, clinic=ctx.clinic, reason_code=args.reason_code, actor=ctx.actor
    )
    return {
        "escalation_id": str(job.id),
        "status": "escalated",
        "message": "A staff member will follow up shortly.",
    }


TOOL = Tool(
    name="escalate_to_human",
    description="Hand the conversation to a staff member.",
    args_model=EscalateArgs,
    handler=_handler,
    required_perms=("ai.use",),
    consequential=False,
    requires_patient=False,
)


__all__ = ["TOOL", "EscalateArgs"]
