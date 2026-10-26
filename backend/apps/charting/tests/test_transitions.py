"""State-machine transitions for tooth layers (T108)."""

from __future__ import annotations

import uuid

import pytest

from apps.charting.models import ToothState, ToothStateEvent
from apps.core.models import AuditEvent

pytestmark = pytest.mark.django_db


def _create(owner, patient, save_batch, key, **change):
    change.setdefault("tooth", 11)
    change.setdefault("condition_code", "caries")
    response = save_batch(owner, patient, [change], key=key)
    assert response.status_code == 200, response.json()
    return response.json()["applied"][0]["id"]


def test_existing_to_planned(owner, patient_factory, save_batch, chart_urls):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    state_id = _create(owner, patient, save_batch, "tr-1", surfaces=["M"])

    response = owner.post(chart_urls.transition(state_id), {"to_status": "planned"})
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["status"] == "planned"
    assert body["version"] == 2
    assert body["plan_item_id"] is None

    state = ToothState.all_objects.get(pk=state_id)
    assert state.status == "planned"
    assert state.version == 2

    event = ToothStateEvent.all_objects.get(tooth_state_id=state_id, event_type="transitioned")
    assert event.before["status"] == "existing"
    assert event.after["status"] == "planned"
    assert event.version == 2

    audit = AuditEvent.objects.get(action="chart.updated", entity_id=str(state_id))
    assert audit.after["status"] == "planned"


def test_planned_to_completed_requires_a_plan_item(
    owner, patient_factory, save_batch, chart_urls
):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    state_id = _create(owner, patient, save_batch, "tr-2", surfaces=["M"])
    assert owner.post(chart_urls.transition(state_id), {"to_status": "planned"}).status_code == 200

    blocked = owner.post(chart_urls.transition(state_id), {"to_status": "completed"})
    assert blocked.status_code == 409
    error = blocked.json()["error"]
    assert error["code"] == "plan_item_missing"
    assert "plan_item_id" in error["field_errors"]
    assert ToothState.all_objects.get(pk=state_id).status == "planned"


def test_planned_to_completed_with_a_plan_item_succeeds_and_audits(
    owner, patient_factory, save_batch, chart_urls
):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    state_id = _create(owner, patient, save_batch, "tr-3", surfaces=["M"])
    owner.post(chart_urls.transition(state_id), {"to_status": "planned"})

    plan_item_id = str(uuid.uuid7())
    response = owner.post(
        chart_urls.transition(state_id),
        {"to_status": "completed", "plan_item_id": plan_item_id},
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["status"] == "completed"
    assert body["plan_item_id"] == plan_item_id
    assert body["version"] == 3

    state = ToothState.all_objects.get(pk=state_id)
    assert state.status == "completed"
    assert str(state.plan_item_id) == plan_item_id

    assert ToothStateEvent.all_objects.filter(
        tooth_state_id=state_id, event_type="transitioned"
    ).count() == 2
    assert AuditEvent.objects.filter(action="chart.updated", entity_id=str(state_id)).count() == 2


def test_invalid_transition_is_a_conflict(owner, patient_factory, save_batch, chart_urls):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    state_id = _create(owner, patient, save_batch, "tr-4", surfaces=["M"])

    response = owner.post(chart_urls.transition(state_id), {"to_status": "completed"})
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "invalid_transition"
    assert error["details"]["from"] == "existing"
    assert ToothState.all_objects.get(pk=state_id).status == "existing"


def test_archiving_through_a_transition(owner, patient_factory, save_batch, chart_urls):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    state_id = _create(owner, patient, save_batch, "tr-5", surfaces=["M"])

    response = owner.post(chart_urls.transition(state_id), {"to_status": "archived"})
    assert response.status_code == 200, response.json()

    state = ToothState.all_objects.get(pk=state_id)
    assert state.archived_at is not None
    assert state.version == 2
    assert (
        ToothStateEvent.all_objects.filter(tooth_state_id=state_id, event_type="archived").count()
        == 1
    )
    assert AuditEvent.objects.filter(action="chart.archived", entity_id=str(state_id)).count() == 1

    # The state is no longer active, so it cannot transition again.
    again = owner.post(chart_urls.transition(state_id), {"to_status": "planned"})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "invalid_transition"


def test_unknown_target_status_is_a_validation_error(
    owner, patient_factory, save_batch, chart_urls
):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    state_id = _create(owner, patient, save_batch, "tr-6")
    response = owner.post(chart_urls.transition(state_id), {"to_status": "banana"})
    assert response.status_code == 400
    assert "to_status" in response.json()["error"]["field_errors"]


def test_foreign_state_is_404(owner, staff_factory, patient_factory, save_batch, chart_urls):
    other = staff_factory(role="owner")
    foreign_patient = patient_factory(other.clinic, date_of_birth="1990-01-01")
    state_id = _create(other, foreign_patient, save_batch, "tr-foreign", surfaces=["M"])

    response = owner.post(chart_urls.transition(state_id), {"to_status": "planned"})
    assert response.status_code == 404


def test_transition_requires_edit_permission(
    staff_factory, patient_factory, save_batch, chart_urls
):
    receptionist = staff_factory(role="receptionist")
    patient = patient_factory(receptionist.clinic, date_of_birth="1990-01-01")

    # The receptionist cannot create the state either, so seed it directly.
    from apps.charting import fdi

    info = fdi.tooth_info(11)
    state = ToothState.all_objects.create(
        clinic=receptionist.clinic,
        patient=patient,
        tooth=11,
        dentition=info["dentition"],
        condition_id="caries",
        status="existing",
        surfaces=["M"],
    )
    response = receptionist.post(chart_urls.transition(state.id), {"to_status": "planned"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


def test_assistant_can_transition(staff_factory, patient_factory, save_batch, chart_urls):
    assistant = staff_factory(role="assistant")
    patient = patient_factory(assistant.clinic, date_of_birth="1990-01-01")
    state_id = _create(assistant, patient, save_batch, "tr-assistant", surfaces=["M"])
    response = assistant.post(chart_urls.transition(state_id), {"to_status": "referred"})
    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "referred"


def test_transition_on_an_archived_patient_is_forbidden(
    owner, patient_factory, save_batch, chart_urls
):
    from django.utils import timezone

    from apps.patients.models import Patient

    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    state_id = _create(owner, patient, save_batch, "tr-archived", surfaces=["M"])
    Patient.all_objects.filter(pk=patient.pk).update(deleted_at=timezone.now())

    response = owner.post(chart_urls.transition(state_id), {"to_status": "planned"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "patient_archived"
