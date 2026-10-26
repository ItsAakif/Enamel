"""Portal authorization: IDOR suite and record-summary allowlist (T121)."""

from __future__ import annotations

import pytest

from apps.patients.models import MedicalHistory

pytestmark = pytest.mark.django_db

INTERNAL_NOTE = "staff-only internal note do not expose"


def test_patient_a_cannot_read_patient_b_appointment(
    owner, patient_factory, portal_login, scheduling_deps, appointment_factory
):
    a = patient_factory(owner.clinic, first_name="Alice")
    b = patient_factory(owner.clinic, first_name="Bob")
    deps = scheduling_deps(owner.clinic, owner.user)
    b_appointment = appointment_factory(owner.clinic, b, deps)

    portal_a = portal_login(owner.clinic, a)
    response = portal_a.get(f"/api/v1/portal/appointments/{b_appointment.id}/")
    assert response.status_code == 404
    # A's own list is empty.
    assert portal_a.get("/api/v1/portal/appointments/").json()["count"] == 0


def test_patient_a_cannot_read_patient_b_plan(owner, patient_factory, portal_login, plan_factory):
    a = patient_factory(owner.clinic, first_name="Alice")
    b = patient_factory(owner.clinic, first_name="Bob")
    b_plan = plan_factory(owner.clinic, b)

    portal_a = portal_login(owner.clinic, a)
    assert portal_a.get(f"/api/v1/portal/plans/{b_plan.id}/").status_code == 404
    assert portal_a.get("/api/v1/portal/plans/").json()["count"] == 0


def test_patient_a_cannot_read_patient_b_invoice(owner, patient_factory, portal_login, invoice_factory):
    a = patient_factory(owner.clinic, first_name="Alice")
    b = patient_factory(owner.clinic, first_name="Bob")
    b_invoice = invoice_factory(owner.clinic, b)

    portal_a = portal_login(owner.clinic, a)
    assert portal_a.get(f"/api/v1/portal/invoices/{b_invoice.id}/").status_code == 404
    assert portal_a.get("/api/v1/portal/invoices/").json()["count"] == 0


def test_patient_a_cannot_download_patient_b_document(
    owner, patient_factory, portal_login, document_factory
):
    a = patient_factory(owner.clinic, first_name="Alice")
    b = patient_factory(owner.clinic, first_name="Bob")
    document_factory(owner.clinic, b)

    portal_a = portal_login(owner.clinic, a)
    assert portal_a.get("/api/v1/portal/documents/").json()["count"] == 0


def test_patient_a_cannot_cancel_patient_b_appointment(
    owner, patient_factory, portal_login, scheduling_deps, appointment_factory
):
    a = patient_factory(owner.clinic, first_name="Alice")
    b = patient_factory(owner.clinic, first_name="Bob")
    deps = scheduling_deps(owner.clinic, owner.user)
    b_appointment = appointment_factory(owner.clinic, b, deps, hours_ahead=72)

    portal_a = portal_login(owner.clinic, a)
    response = portal_a.post(f"/api/v1/portal/appointments/{b_appointment.id}/cancel/")
    assert response.status_code == 404
    b_appointment.refresh_from_db()
    assert b_appointment.status == "scheduled"


def test_patient_a_cannot_accept_patient_b_plan_item(
    owner, patient_factory, portal_login, plan_factory
):
    from apps.plans.models import PlanItem

    a = patient_factory(owner.clinic, first_name="Alice")
    b = patient_factory(owner.clinic, first_name="Bob")
    b_plan = plan_factory(owner.clinic, b)
    b_item = PlanItem.all_objects.filter(plan=b_plan).first()

    portal_a = portal_login(owner.clinic, a)
    response = portal_a.post(
        f"/api/v1/portal/plans/{b_plan.id}/items/{b_item.id}/accept/"
    )
    assert response.status_code == 404


def test_record_summary_omits_staff_only_fields(owner, patient_factory, portal_login):
    from apps.portal.services.records import SUMMARY_KEYS

    patient = patient_factory(owner.clinic, notes=INTERNAL_NOTE)
    MedicalHistory.all_objects.create(
        clinic=owner.clinic,
        patient=patient,
        kind="condition",
        label="Hypertension",
        notes=INTERNAL_NOTE,
    )
    from apps.patients.models import Encounter

    Encounter.all_objects.create(
        clinic=owner.clinic,
        patient=patient,
        kind="exam",
        status="finalized",
        chief_complaint="checkup",
        notes=INTERNAL_NOTE,
        diagnosis_codes=["Z01"],
    )

    portal = portal_login(owner.clinic, patient)
    response = portal.get("/api/v1/portal/records/")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == SUMMARY_KEYS

    text = response.content.decode()
    assert INTERNAL_NOTE not in text
    assert "notes" not in text
    assert "recorded_by" not in text
    assert "diagnosis_codes" not in text
    assert body["medical_history"][0]["label"] == "Hypertension"
