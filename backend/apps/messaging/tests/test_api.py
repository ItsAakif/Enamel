"""Messaging HTTP surface (T114 APIs)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.messaging.models import Message, MessageTemplate, Suppression
from apps.messaging.services import dispatch
from apps.patients.models import Encounter

from .conftest import DAYTIME

pytestmark = pytest.mark.django_db

CONSENTS = "/api/v1/consents/"
SUPPRESSIONS = "/api/v1/suppressions/"
TEMPLATES = "/api/v1/message-templates/"
MESSAGES = "/api/v1/messages/"
RECALL_TYPES = "/api/v1/recall-types/"
RECALL_DUE = "/api/v1/recall/due/"


def test_consent_create_list_and_revoke(owner, patient_factory):
    patient = patient_factory(owner.clinic)

    created = owner.post(
        CONSENTS,
        {
            "patient": str(patient.id),
            "channel": "email",
            "purpose": "appointment_reminders",
            "source": "staff",
            "wording_version": "2026-01",
        },
    )
    assert created.status_code == 201, created.json()
    consent_id = created.json()["id"]
    assert created.json()["status"] == "granted"

    listing = owner.get(CONSENTS, {"patient": str(patient.id)})
    assert listing.status_code == 200
    assert listing.json()["count"] == 1

    revoked = owner.post(f"{CONSENTS}{consent_id}/revoke/")
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    assert Suppression.all_objects.filter(clinic=owner.clinic).count() == 4


def test_suppressions_are_listed(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    from apps.messaging.services import suppression

    suppression.record_opt_out(patient, channel="sms", reason="stop")

    response = owner.get(SUPPRESSIONS)

    assert response.status_code == 200
    assert response.json()["count"] == 4


def test_template_list_and_patch_bumps_version(owner, templates):
    templates(owner.clinic)

    listing = owner.get(TEMPLATES)
    assert listing.status_code == 200
    assert listing.json()["count"] == 3

    template = MessageTemplate.all_objects.get(clinic=owner.clinic, key="appointment_reminder")
    patched = owner.patch(
        f"{TEMPLATES}{template.id}/",
        {"body": "Hi {first_name}, see you {date} at {time} with {provider}."},
    )
    assert patched.status_code == 200, patched.json()
    assert patched.json()["version"] == 2

    template.refresh_from_db()
    assert template.version == 2


def test_message_list_filters(owner, patient_factory, templates, grant_consent):
    templates(owner.clinic)
    patient = patient_factory(owner.clinic)
    grant_consent(patient)
    dispatch.send(
        patient,
        "email",
        "appointment_reminders",
        "appointment_reminder",
        {"first_name": "Ada", "date": "Mon", "time": "10:00", "provider": "Dr"},
        idempotency_key="api-msg-1",
        now=DAYTIME,
    )

    response = owner.get(MESSAGES, {"status": "sent", "channel": "email"})

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert Message.all_objects.filter(clinic=owner.clinic).count() == 1


def test_recall_types_due_and_schedule(owner, patient_factory, templates):
    templates(owner.clinic)
    created = owner.post(
        RECALL_TYPES,
        {
            "name": "Six month check-up",
            "interval_months": 6,
            "channel": "email",
            "template_key": "recall_reminder",
        },
    )
    assert created.status_code == 201, created.json()
    recall_type_id = created.json()["id"]

    patient = patient_factory(owner.clinic)
    Encounter.all_objects.create(
        clinic_id=owner.clinic.id,
        patient=patient,
        kind="treatment",
        occurred_at=datetime(2025, 1, 5, tzinfo=UTC),
        status=Encounter.Status.FINALIZED,
    )

    due = owner.get(RECALL_DUE, {"as_of": "2025-07-10T00:00:00Z"})
    assert due.status_code == 200
    assert len(due.json()) == 1
    assert due.json()[0]["patient_id"] == str(patient.id)

    scheduled = owner.post(
        f"/api/v1/recall/{patient.id}/schedule/", {"recall_type": recall_type_id}
    )
    assert scheduled.status_code == 201, scheduled.json()
    assert scheduled.json()["status"] == "skipped"


def test_recall_due_requires_recall_manage_permission(assistant):
    response = assistant.get(RECALL_DUE)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


def test_consents_require_messaging_view_permission(assistant):
    response = assistant.get(CONSENTS)
    assert response.status_code == 403
