"""Dispatcher gate order, idempotency and retries (T114 acceptance 1, 6, 7)."""

from __future__ import annotations

import pytest

from apps.messaging.adapters import AdapterError
from apps.messaging.models import Message
from apps.messaging.services import dispatch

from .conftest import DAYTIME

pytestmark = pytest.mark.django_db

REMINDER_CONTEXT = {"first_name": "Ada", "date": "Mon", "time": "10:00", "provider": "Dr"}


class FlakyAdapter:
    name = "flaky"

    def __init__(self, fail_times: int = 1) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def send(self, message):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise AdapterError("temporary")
        return f"flaky-{message.id}"


class AlwaysFailAdapter:
    name = "always-fail"

    def send(self, message):
        raise AdapterError("down")


def test_no_consent_skips_without_calling_the_adapter(owner, patient_factory, templates):
    templates(owner.clinic)
    patient = patient_factory(owner.clinic)

    message = dispatch.send(
        patient,
        "email",
        "appointment_reminders",
        "appointment_reminder",
        REMINDER_CONTEXT,
        idempotency_key="no-consent-1",
        now=DAYTIME,
    )

    assert message.status == Message.Status.SKIPPED
    assert message.reason == "no_consent"
    assert message.attempts == 0
    assert message.provider_message_id == ""


def test_dev_send_marks_sent_with_deterministic_provider_id(
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
        idempotency_key="sent-1",
        now=DAYTIME,
    )

    assert message.status == Message.Status.SENT
    assert message.provider == "dev"
    assert message.provider_message_id == f"dev-{message.id}"
    assert message.sent_at is not None


def test_idempotency_key_produces_one_message(owner, patient_factory, templates, grant_consent):
    templates(owner.clinic)
    patient = patient_factory(owner.clinic)
    grant_consent(patient)

    first = dispatch.send(
        patient,
        "email",
        "appointment_reminders",
        "appointment_reminder",
        REMINDER_CONTEXT,
        idempotency_key="duplicate-1",
        now=DAYTIME,
    )
    second = dispatch.send(
        patient,
        "email",
        "appointment_reminders",
        "appointment_reminder",
        REMINDER_CONTEXT,
        idempotency_key="duplicate-1",
        now=DAYTIME,
    )

    assert first.id == second.id
    assert Message.all_objects.filter(clinic=owner.clinic, idempotency_key="duplicate-1").count() == 1


def test_deceased_patient_is_skipped(owner, patient_factory, templates, grant_consent):
    templates(owner.clinic)
    patient = patient_factory(owner.clinic, status="deceased")
    grant_consent(patient)

    message = dispatch.send(
        patient,
        "email",
        "appointment_reminders",
        "appointment_reminder",
        REMINDER_CONTEXT,
        idempotency_key="deceased-1",
        now=DAYTIME,
    )

    assert message.status == Message.Status.SKIPPED
    assert message.reason == "patient_inactive"


def test_adapter_retry_succeeds_without_duplication(
    owner, patient_factory, templates, grant_consent
):
    templates(owner.clinic)
    patient = patient_factory(owner.clinic)
    grant_consent(patient)
    adapter = FlakyAdapter(fail_times=1)

    message = dispatch.send(
        patient,
        "email",
        "appointment_reminders",
        "appointment_reminder",
        REMINDER_CONTEXT,
        idempotency_key="retry-ok-1",
        now=DAYTIME,
        adapter=adapter,
    )
    assert message.status == Message.Status.QUEUED
    assert message.attempts == 1
    assert message.error_code == "adapter_error"

    result = dispatch.handle_deliver_message({"message_id": str(message.id)}, adapter=adapter)

    message.refresh_from_db()
    assert result["status"] == Message.Status.SENT
    assert message.status == Message.Status.SENT
    assert message.attempts == 2
    assert Message.all_objects.filter(clinic=owner.clinic).count() == 1


def test_adapter_failure_retries_then_marks_failed(owner, patient_factory, templates, grant_consent):
    templates(owner.clinic)
    patient = patient_factory(owner.clinic)
    grant_consent(patient)
    adapter = AlwaysFailAdapter()

    message = dispatch.send(
        patient,
        "email",
        "appointment_reminders",
        "appointment_reminder",
        REMINDER_CONTEXT,
        idempotency_key="retry-fail-1",
        now=DAYTIME,
        adapter=adapter,
    )
    assert message.attempts == 1

    dispatch.handle_deliver_message({"message_id": str(message.id)}, adapter=adapter)
    dispatch.handle_deliver_message({"message_id": str(message.id)}, adapter=adapter)

    message.refresh_from_db()
    assert message.status == Message.Status.FAILED
    assert message.attempts == 3
    assert message.error_code == "adapter_error"
    assert Message.all_objects.filter(clinic=owner.clinic).count() == 1

    # A further retry of a finished message is a no-op.
    dispatch.handle_deliver_message({"message_id": str(message.id)}, adapter=adapter)
    message.refresh_from_db()
    assert message.attempts == 3
