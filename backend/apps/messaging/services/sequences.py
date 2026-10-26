"""Appointment reminder sequences (T114).

Every appointment that is ``scheduled`` or ``confirmed`` gets the applicable
reminder steps as durable ``JobRequest`` rows (T-7 days, T-2 days and the
morning of, all at a fixed clinic-local hour). Each step has a deterministic
idempotency key (``reminder:<appointment>:<step>``), so re-saving an appointment
replaces - never duplicates - its pending steps.

``resequence_appointment`` is wired to ``scheduling.Appointment`` with a
``post_save`` signal in ``apps.MessagingConfig.ready``. Cancelling (or
completing) an appointment deletes its pending steps; rescheduling cancels the
old appointment and creates a fresh one, so the steps move with it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from datetime import time as time_type
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.jobs import durable_task, enqueue
from apps.core.models import JobRequest
from apps.core.tenancy import with_clinic
from apps.scheduling.models import Appointment
from apps.scheduling.services.availability import clinic_timezone

from ..models import Consent
from . import consent as consent_service
from . import dispatch

REMINDER_JOB = "messaging.reminder"
REMINDER_QUEUE = "reminders"
REMINDER_TEMPLATE = "appointment_reminder"
REMINDER_PURPOSE = Consent.Purpose.APPOINTMENT_REMINDERS

#: Statuses that still deserve reminders. A held/requested/cancelled/completed
#: appointment has none.
REMINDER_STATUSES = frozenset({"scheduled", "confirmed"})

#: ``(step key, days before the appointment)`` for the timed steps.
TIMED_STEPS: tuple[tuple[str, int], ...] = (("t_minus_7", 7), ("t_minus_2", 2))
REMINDER_HOUR = 9
MORNING_OF_HOUR = 8


def preferred_channel(patient: Any) -> str:
    """Pick the channel with the best consent/address coverage for reminders."""

    email = str(getattr(patient, "email", "") or "").strip()
    phone = str(getattr(patient, "phone_e164", "") or "").strip()
    if email and consent_service.is_granted(
        patient, Consent.Channel.EMAIL, REMINDER_PURPOSE
    ):
        return Consent.Channel.EMAIL
    if phone and consent_service.is_granted(
        patient, Consent.Channel.SMS, REMINDER_PURPOSE
    ):
        return Consent.Channel.SMS
    if email:
        return Consent.Channel.EMAIL
    if phone:
        return Consent.Channel.SMS
    return Consent.Channel.EMAIL


def reminder_steps(
    clinic: Any, appointment: Appointment, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Return the reminder steps still in the future for ``appointment``."""

    moment = now or timezone.now()
    tz = clinic_timezone(clinic)
    start_local = appointment.start_at.astimezone(tz)
    steps: list[dict[str, Any]] = []

    def _add(key: str, local_dt: datetime) -> None:
        scheduled_for = local_dt.astimezone(UTC)
        if moment < scheduled_for < appointment.start_at:
            steps.append({"key": key, "scheduled_for": scheduled_for})

    for key, days in TIMED_STEPS:
        day = (start_local - timedelta(days=days)).date()
        _add(key, datetime.combine(day, time_type(REMINDER_HOUR, 0), tzinfo=tz))
    morning = start_local.date()
    _add(
        "morning_of",
        datetime.combine(morning, time_type(MORNING_OF_HOUR, 0), tzinfo=tz),
    )
    return steps


def cancel_pending_reminders(appointment: Appointment) -> int:
    """Delete the appointment's pending reminder jobs; return the count."""

    deleted, _ = JobRequest.objects.filter(
        kind=REMINDER_JOB,
        status=JobRequest.Status.PENDING,
        payload__appointment_id=str(appointment.id),
    ).delete()
    return deleted


def resequence_appointment(
    appointment: Appointment, *, now: datetime | None = None
) -> dict[str, int]:
    """Replace the pending reminder steps for ``appointment`` (idempotent)."""

    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(appointment.clinic_id):
            cancelled = cancel_pending_reminders(appointment)
            if appointment.status not in REMINDER_STATUSES:
                return {"cancelled": cancelled, "created": 0}

            clinic = appointment.clinic
            patient = appointment.patient
            channel = preferred_channel(patient)
            created = 0
            for step in reminder_steps(clinic, appointment, now=moment):
                enqueue(
                    REMINDER_JOB,
                    {
                        "appointment_id": str(appointment.id),
                        "step": step["key"],
                        "template_key": REMINDER_TEMPLATE,
                        "channel": channel,
                        "purpose": REMINDER_PURPOSE,
                    },
                    due_at=step["scheduled_for"],
                    idempotency_key=f"reminder:{appointment.id}:{step['key']}",
                    clinic=clinic,
                )
                created += 1
    return {"cancelled": cancelled, "created": created}


def reminder_context(appointment: Appointment) -> dict[str, Any]:
    """Build the whitelisted template context for a reminder."""

    clinic = appointment.clinic
    tz = clinic_timezone(clinic)
    start_local = appointment.start_at.astimezone(tz)
    provider = getattr(appointment, "provider", None)
    return {
        "first_name": appointment.patient.first_name,
        "date": start_local.strftime("%a %d %b %Y"),
        "time": start_local.strftime("%H:%M"),
        "provider": getattr(provider, "display_name", "") or "",
    }


@durable_task(REMINDER_JOB, queue=REMINDER_QUEUE)
def handle_reminder(payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any] | None:
    """Durable handler: send one reminder step through the dispatcher."""

    appointment_id = (payload or {}).get("appointment_id")
    if not appointment_id:
        return None
    appointment = Appointment.all_objects.filter(pk=appointment_id).first()
    if appointment is None:
        return None
    if appointment.status not in REMINDER_STATUSES:
        return {"appointment_id": str(appointment.id), "skipped": "status"}

    patient = appointment.patient
    channel = payload.get("channel") or preferred_channel(patient)
    template_key = payload.get("template_key") or REMINDER_TEMPLATE
    message = dispatch.send(
        patient,
        channel,
        REMINDER_PURPOSE,
        template_key,
        reminder_context(appointment),
        idempotency_key=f"reminder:{appointment.id}:{payload.get('step')}",
        now=now,
    )
    return {"message_id": str(message.id), "status": message.status}


__all__ = [
    "MORNING_OF_HOUR",
    "REMINDER_HOUR",
    "REMINDER_JOB",
    "REMINDER_PURPOSE",
    "REMINDER_QUEUE",
    "REMINDER_STATUSES",
    "REMINDER_TEMPLATE",
    "TIMED_STEPS",
    "cancel_pending_reminders",
    "handle_reminder",
    "preferred_channel",
    "reminder_context",
    "reminder_steps",
    "resequence_appointment",
]
