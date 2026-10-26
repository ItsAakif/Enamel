"""Plan lifecycle and item completion (T117)."""

from __future__ import annotations

import pytest

from apps.charting.models import ToothState
from apps.plans.models import PlanItem, TreatmentPlan
from apps.plans.services import plans as plan_service

pytestmark = pytest.mark.django_db

PLANS = "/api/v1/treatment-plans/"


def _plan_with_item(owner, patient_factory, plan_factory, tooth_state_factory, *, tooth=16):
    patient = patient_factory(owner.clinic)
    state = tooth_state_factory(patient, tooth=tooth, status="planned")
    plan = plan_factory(
        owner.clinic,
        patient,
        items=[
            {
                "procedure_code": "D2391",
                "tooth": tooth,
                "quantity": 1,
                "unit_price_minor": 150_000,
                "tax_rate_bp": 1800,
                "tooth_state_id": state.id,
                "status": PlanItem.Status.PROPOSED,
            }
        ],
    )
    return plan, state


def test_valid_transitions(owner, patient_factory, plan_factory, tooth_state_factory):
    plan, _ = _plan_with_item(owner, patient_factory, plan_factory, tooth_state_factory)
    presented = owner.post(f"{PLANS}{plan.id}/present/")
    assert presented.status_code == 200, presented.json()
    assert presented.json()["status"] == "presented"

    accepted = owner.post(f"{PLANS}{plan.id}/accept/")
    assert accepted.status_code == 200, accepted.json()
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["accepted_by"] == str(owner.user.id)


def test_invalid_transition_is_409(owner, patient_factory, plan_factory, tooth_state_factory):
    plan, _ = _plan_with_item(owner, patient_factory, plan_factory, tooth_state_factory)
    response = owner.post(f"{PLANS}{plan.id}/accept/")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "invalid_transition"
    assert error["details"]["from"] == "draft"
    assert error["details"]["to"] == "accepted"

    owner.post(f"{PLANS}{plan.id}/present/")
    owner.post(f"{PLANS}{plan.id}/decline/")
    again = owner.post(f"{PLANS}{plan.id}/accept/")
    assert again.status_code == 409


def test_unknown_action_is_404(owner, patient_factory, plan_factory, tooth_state_factory):
    plan, _ = _plan_with_item(owner, patient_factory, plan_factory, tooth_state_factory)
    response = owner.post(f"{PLANS}{plan.id}/explode/")
    assert response.status_code == 404


def test_completing_item_sets_tooth_state_completed(
    owner, patient_factory, plan_factory, tooth_state_factory
):
    plan, state = _plan_with_item(
        owner, patient_factory, plan_factory, tooth_state_factory, tooth=26
    )
    item = PlanItem.all_objects.get(plan=plan)

    completed = plan_service.complete_item(item, actor=owner.user)

    assert completed.status == PlanItem.Status.COMPLETED
    state.refresh_from_db()
    assert state.status == ToothState.Status.COMPLETED
    assert state.plan_item_id == item.id
    assert state.version == 2


def test_completing_item_is_idempotent(
    owner, patient_factory, plan_factory, tooth_state_factory
):
    plan, state = _plan_with_item(
        owner, patient_factory, plan_factory, tooth_state_factory, tooth=36
    )
    item = PlanItem.all_objects.get(plan=plan)
    plan_service.complete_item(item, actor=owner.user)
    state.refresh_from_db()
    plan_service.complete_item(item, actor=owner.user)
    state.refresh_from_db()
    assert state.version == 2  # not bumped twice


def test_assistant_cannot_create_or_approve(assistant, patient_factory):
    patient = patient_factory(assistant.clinic)
    created = assistant.post(
        PLANS, {"patient": str(patient.id), "title": "nope", "items": []}
    )
    assert created.status_code == 403
    assert created.json()["error"]["code"] == "permission_denied"


def test_plan_list_filters_by_status(owner, patient_factory, plan_factory, tooth_state_factory):
    plan, _ = _plan_with_item(owner, patient_factory, plan_factory, tooth_state_factory)
    owner.post(f"{PLANS}{plan.id}/present/")
    listed = owner.get(PLANS, {"status": "presented"})
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert TreatmentPlan.all_objects.get(pk=plan.id).status == "presented"


def test_mutations_write_audit_rows(owner, patient_factory, plan_factory, tooth_state_factory):
    from apps.core.models import AuditEvent

    plan, _ = _plan_with_item(owner, patient_factory, plan_factory, tooth_state_factory)
    owner.post(f"{PLANS}{plan.id}/present/")

    actions = set(
        AuditEvent.objects.filter(clinic_id=owner.clinic.id).values_list("action", flat=True)
    )
    assert "plan.created" in actions
    assert "plan.presented" in actions
