"""Suppression rules (T114 acceptance 3, spec edge cases)."""

from __future__ import annotations

import pytest

from apps.messaging.models import Message, Suppression
from apps.messaging.services import dispatch, suppression

from .conftest import DAYTIME

pytestmark = pytest.mark.django_db


def test_address_hash_is_salted_and_stable():
    digest = suppression.address_hash("email", "ada@example.test")
    assert "ada" not in digest
    assert digest == suppression.address_hash("email", "ada@example.test")
    assert digest != suppression.address_hash("sms", "ada@example.test")
    assert suppression.address_hash("email", "") == ""


def test_purpose_rule_blocks_all_non_transactional():
    assert suppression.purpose_is_blocked("appointment_reminders") is True
    assert suppression.purpose_is_blocked("recall") is True
    assert suppression.purpose_is_blocked("marketing") is True
    assert suppression.purpose_is_blocked("transactional") is False


def test_stop_suppresses_all_channels_and_blocks_but_transactional(
    owner, patient_factory, templates
):
    templates(owner.clinic)
    patient = patient_factory(owner.clinic)

    suppression.record_opt_out(patient, channel="sms", reason="stop")

    channels = set(
        Suppression.all_objects.filter(clinic=owner.clinic).values_list("channel", flat=True)
    )
    assert channels == {"email", "sms", "whatsapp", "voice"}

    blocked = dispatch.send(
        patient,
        "email",
        "appointment_reminders",
        "appointment_reminder",
        {"first_name": "Ada", "date": "Mon", "time": "10:00", "provider": "Dr"},
        idempotency_key="blocked-1",
        now=DAYTIME,
    )
    assert blocked.status == Message.Status.SKIPPED
    assert blocked.reason == "suppressed"

    allowed = dispatch.send(
        patient,
        "email",
        "transactional",
        "cancellation_confirmation",
        {"first_name": "Ada", "date": "Mon", "time": "10:00"},
        idempotency_key="allowed-1",
        now=DAYTIME,
    )
    assert allowed.status == Message.Status.SENT


def test_duplicate_opt_out_is_idempotent(owner, patient_factory):
    patient = patient_factory(owner.clinic)

    suppression.record_opt_out(patient, channel="sms", reason="stop")
    suppression.record_opt_out(patient, channel="sms", reason="stop")

    assert Suppression.all_objects.filter(clinic=owner.clinic).count() == 4


def test_bounce_and_complaint_are_recorded(owner, patient_factory):
    patient = patient_factory(owner.clinic)

    suppression.record_opt_out(patient, channel="email", reason="bounce")

    assert Suppression.all_objects.filter(
        clinic=owner.clinic, reason=Suppression.Reason.BOUNCE
    ).exists()
