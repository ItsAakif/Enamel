"""Quiet hours and frequency caps (T114 acceptance 4, 5)."""

from __future__ import annotations

import pytest

from apps.core.models import JobRequest
from apps.messaging.models import Message
from apps.messaging.services import dispatch, policy

from .conftest import DAYTIME, KOLKATA, NIGHT

pytestmark = pytest.mark.django_db

REMINDER_CONTEXT = {"first_name": "Ada", "date": "Mon", "time": "10:00", "provider": "Dr"}


def test_quiet_window_detection(owner):
    assert policy.in_quiet_hours(owner.clinic, NIGHT) is True
    assert policy.in_quiet_hours(owner.clinic, DAYTIME) is False


def test_next_window_start_is_the_morning(owner):
    start = policy.next_window_start(owner.clinic, NIGHT)
    local = start.astimezone(KOLKATA)
    assert local.hour == 8
    assert local.minute == 0
    assert local.date().isoformat() == "2026-01-06"


def test_quiet_hours_defers_then_sends_exactly_once(
    owner, patient_factory, templates, grant_consent
):
    templates(owner.clinic)
    patient = patient_factory(owner.clinic)
    grant_consent(patient)

    message = dispatch.send(
        patient,
        "email",
        "appointment_reminders",
        "appointment_reminder",
        REMINDER_CONTEXT,
        idempotency_key="quiet-1",
        now=NIGHT,
    )

    assert message.status == Message.Status.QUEUED
    assert message.reason == "quiet_hours"
    assert message.scheduled_for is not None

    jobs = JobRequest.objects.filter(kind=dispatch.MESSAGE_DELIVER_JOB)
    assert jobs.count() == 1
    assert jobs.first().due_at == message.scheduled_for

    dispatch.handle_deliver_message({"message_id": str(message.id)})
    message.refresh_from_db()
    assert message.status == Message.Status.SENT

    # Running the deferred job again must not send a second message.
    dispatch.handle_deliver_message({"message_id": str(message.id)})
    message.refresh_from_db()
    assert message.status == Message.Status.SENT
    assert Message.all_objects.filter(clinic=owner.clinic).count() == 1


def test_frequency_cap_blocks_the_fifth(owner, patient_factory, templates, grant_consent):
    templates(owner.clinic)
    patient = patient_factory(owner.clinic)
    grant_consent(patient)

    for index in range(4):
        sent = dispatch.send(
            patient,
            "email",
            "appointment_reminders",
            "appointment_reminder",
            REMINDER_CONTEXT,
            idempotency_key=f"cap-{index}",
            now=DAYTIME,
        )
        assert sent.status == Message.Status.SENT

    fifth = dispatch.send(
        patient,
        "email",
        "appointment_reminders",
        "appointment_reminder",
        REMINDER_CONTEXT,
        idempotency_key="cap-4",
        now=DAYTIME,
    )

    assert fifth.status == Message.Status.SKIPPED
    assert fifth.reason == "capped"
    assert fifth.provider_message_id == ""
