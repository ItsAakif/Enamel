"""Call escalation (T124).

Escalation has two halves and both are durable:

* a ``core.JobRequest`` of kind ``ai.escalation`` -- the staff task, written in
  the caller's transaction by :func:`apps.core.jobs.enqueue`; and
* the call record moved to ``escalated`` with an ``ended_at``, which is what
  ends the AI session.

The job payload carries ids and a reason *code* only -- never transcript text,
so an escalated call cannot leak PHI into the job table.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.core.audit import record as record_audit
from apps.core.jobs import enqueue

from ..models import VoiceCall, VoiceTurn

ESCALATION_KIND = "ai.escalation"


def _next_ordinal(call: VoiceCall) -> int:
    last = (
        VoiceTurn.all_objects.filter(call=call)
        .order_by("-ordinal")
        .values_list("ordinal", flat=True)
        .first()
    )
    return int(last or 0) + 1


def escalate_call(
    call: Any,
    *,
    clinic: Any = None,
    reason_code: str = "caller_request",
    actor: Any = None,
) -> Any:
    """Create the staff task and end the AI session. Returns the ``JobRequest``."""

    resolved_clinic = clinic or getattr(call, "clinic", None)
    payload: dict[str, Any] = {"reason_code": str(reason_code or "caller_request")}
    idempotency_key = None
    if call is not None:
        payload["call_id"] = str(call.id)
        idempotency_key = f"{ESCALATION_KIND}:{call.id}"

    job = enqueue(
        ESCALATION_KIND,
        payload=payload,
        clinic=resolved_clinic,
        idempotency_key=idempotency_key,
    )

    if call is not None:
        call.status = VoiceCall.Status.ESCALATED
        call.outcome = "escalated"
        if call.ended_at is None:
            call.ended_at = timezone.now()
        call.save(update_fields=["status", "outcome", "ended_at", "updated_at"])

        VoiceTurn.all_objects.create(
            clinic_id=call.clinic_id,
            call=call,
            role=VoiceTurn.Role.SYSTEM,
            kind=VoiceTurn.Kind.ESCALATION,
            ordinal=_next_ordinal(call),
            text="A staff member will follow up shortly.",
        )
        record_audit(
            "ai.call.escalated",
            entity=call,
            after={"status": "escalated", "reason_code": payload["reason_code"]},
            actor=actor,
        )

    return job


__all__ = ["ESCALATION_KIND", "escalate_call"]
