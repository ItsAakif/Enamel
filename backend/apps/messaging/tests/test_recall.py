"""Dental recall due dates and scheduling (T114 recall acceptance)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.messaging.models import Message, RecallStatus, RecallType
from apps.messaging.services import recall as recall_service
from apps.patients.models import Encounter

from .conftest import DAYTIME

pytestmark = pytest.mark.django_db


def _finalized(patient, occurred_at):
    return Encounter.all_objects.create(
        clinic_id=patient.clinic_id,
        patient=patient,
        kind="treatment",
        occurred_at=occurred_at,
        status=Encounter.Status.FINALIZED,
    )


@pytest.fixture
def recall_type(owner):
    return RecallType.all_objects.create(
        clinic=owner.clinic,
        name="Six month check-up",
        interval_months=6,
        channel="email",
        template_key="recall_reminder",
    )


def test_due_date_respects_interval_and_completed_encounter(owner, patient_factory, recall_type):
    patient = patient_factory(owner.clinic)
    _finalized(patient, datetime(2025, 1, 5, tzinfo=UTC))

    due = recall_service.recall_due(owner.clinic, as_of=datetime(2025, 7, 10, tzinfo=UTC))
    assert len(due) == 1
    assert due[0]["due_at"] == datetime(2025, 7, 5, tzinfo=UTC)
    assert due[0]["last_completed_at"] == datetime(2025, 1, 5, tzinfo=UTC)
    assert due[0]["status"] == RecallStatus.Status.DUE

    assert recall_service.recall_due(owner.clinic, as_of=datetime(2025, 7, 1, tzinfo=UTC)) == []


def test_later_completed_encounter_pushes_the_due_date(owner, patient_factory, recall_type):
    patient = patient_factory(owner.clinic)
    _finalized(patient, datetime(2025, 1, 5, tzinfo=UTC))
    _finalized(patient, datetime(2025, 6, 1, tzinfo=UTC))

    assert recall_service.recall_due(owner.clinic, as_of=datetime(2025, 11, 1, tzinfo=UTC)) == []
    due = recall_service.recall_due(owner.clinic, as_of=datetime(2025, 12, 2, tzinfo=UTC))
    assert due[0]["due_at"] == datetime(2025, 12, 1, tzinfo=UTC)


def test_schedule_enqueues_a_gated_message(owner, patient_factory, templates, recall_type):
    templates(owner.clinic)
    patient = patient_factory(owner.clinic)
    _finalized(patient, datetime(2025, 1, 5, tzinfo=UTC))

    result = recall_service.schedule(patient, recall_type, now=DAYTIME)

    message = Message.all_objects.get(pk=result["message_id"])
    assert message.purpose == "recall"
    assert message.status == Message.Status.SKIPPED
    assert message.reason == "no_consent"

    status = RecallStatus.all_objects.get(pk=result["recall_status_id"])
    assert status.status == RecallStatus.Status.SCHEDULED


def test_schedule_sends_when_consented(
    owner, patient_factory, templates, grant_consent, recall_type
):
    templates(owner.clinic)
    patient = patient_factory(owner.clinic, email="recall@example.test")
    grant_consent(patient, "email", "recall")
    _finalized(patient, datetime(2025, 1, 5, tzinfo=UTC))

    result = recall_service.schedule(patient, recall_type, now=DAYTIME)

    message = Message.all_objects.get(pk=result["message_id"])
    assert message.status == Message.Status.SENT


def test_complete_resets_the_due_date(owner, patient_factory, recall_type):
    patient = patient_factory(owner.clinic)
    _finalized(patient, datetime(2025, 1, 5, tzinfo=UTC))
    recall_service.recall_due(owner.clinic, as_of=datetime(2025, 8, 1, tzinfo=UTC))

    status = recall_service.complete(patient, recall_type, completed_at=datetime(2025, 8, 1, tzinfo=UTC))

    assert status.status == RecallStatus.Status.COMPLETED
    assert status.due_at == datetime(2026, 2, 1, tzinfo=UTC)
    assert status.last_completed_at == datetime(2025, 8, 1, tzinfo=UTC)
