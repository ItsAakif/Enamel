"""Durable job handlers for the AI app (T124).

``ai.escalation`` is the staff task created when a call is handed to a human.
The handler is a no-op acknowledgement: the durable row *is* the task, and a
real notifier (messaging/recall) can subscribe later without changing the
escalation contract.
"""

from __future__ import annotations

from typing import Any

from apps.core.jobs import durable_task

AI_QUEUE = "ai"


@durable_task("ai.escalation", queue=AI_QUEUE)
def handle_escalation(payload: dict[str, Any]) -> dict[str, Any]:
    """Acknowledge an escalated call. Payload carries ids and a reason code only."""

    return {
        "escalated": True,
        "call_id": payload.get("call_id"),
        "reason_code": payload.get("reason_code", "caller_request"),
    }


__all__ = ["handle_escalation"]
