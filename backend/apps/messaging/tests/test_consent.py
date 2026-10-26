"""Consent registry behaviour (T114 acceptance 1, 2)."""

from __future__ import annotations

import pytest

from apps.core.models import AuditEvent
from apps.messaging.models import Consent, Message, Suppression
from apps.messaging.services import consent as consent_service
from apps.messaging.services import dispatch

from .conftest import NIGHT

pytestmark = pytest.mark.django_db


def test_grant_is_idempotent_and_refreshes_evidence(owner, patient_factory):
    patient = patient_factory(owner.clinic)

    first = consent_service.grant(
        patient, "email", "appointment_reminders", source="staff", wording_version="2026-01"
    )
    second = consent_service.grant(
        patient, "email", "appointment_reminders", source="portal", wording_version="2026-02"
    )

    assert first.id == second.id
    assert Consent.all_objects.filter(patient=patient, status="granted").count() == 1
    assert second.source == "portal"
    assert second.wording_version == "2026-02"


def test_is_granted_reflects_state(owner, patient_factory, grant_consent):
    patient = patient_factory(owner.clinic)
    assert consent_service.is_granted(patient, "email", "appointment_reminders") is False
    grant_consent(patient)
    assert consent_service.is_granted(patient, "email", "appointment_reminders") is True


def test_revoke_is_immediate_cross_channel_and_skips_queued(
    owner, patient_factory, templates, grant_consent
):
    templates(owner.clinic)
    patient = patient_factory(owner.clinic)
    consent = grant_consent(patient, "email", "appointment_reminders")

    queued = dispatch.send(
        patient,
        "email",
        "appointment_reminders",
        "appointment_reminder",
        {"first_name": "Ada", "date": "Mon", "time": "10:00", "provider": "Dr"},
        idempotency_key="queued-1",
        now=NIGHT,
    )
    assert queued.status == Message.Status.QUEUED

    consent_service.revoke(consent)

    queued.refresh_from_db()
    assert queued.status == Message.Status.SKIPPED
    assert queued.reason == "consent_revoked"

    channels = set(
        Suppression.all_objects.filter(clinic=owner.clinic).values_list("channel", flat=True)
    )
    assert channels == {"email", "sms", "whatsapp", "voice"}

    consent.refresh_from_db()
    assert consent.status == Consent.Status.REVOKED
    assert consent.revoked_at is not None


def test_revoke_writes_an_audit_event(owner, patient_factory, grant_consent):
    patient = patient_factory(owner.clinic)
    consent = grant_consent(patient)

    consent_service.revoke(consent)

    assert (
        AuditEvent.objects.filter(
            clinic=owner.clinic, action="messaging.consent_revoked"
        ).count()
        == 1
    )
