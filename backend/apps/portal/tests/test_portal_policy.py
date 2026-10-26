"""Portal self-service policy: cutoffs, plan items and document download (T121)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.plans.models import PlanItem

pytestmark = pytest.mark.django_db


def test_cancel_inside_policy_succeeds(
    owner, patient_factory, portal_login, scheduling_deps, appointment_factory
):
    patient = patient_factory(owner.clinic)
    deps = scheduling_deps(owner.clinic, owner.user)
    appointment = appointment_factory(owner.clinic, patient, deps, hours_ahead=72)

    portal = portal_login(owner.clinic, patient)
    response = portal.post(f"/api/v1/portal/appointments/{appointment.id}/cancel/")
    assert response.status_code == 200, response.content
    assert response.json()["status"] == "cancelled"

    appointment.refresh_from_db()
    assert appointment.status == "cancelled"
    assert AuditEvent.objects.filter(action="portal.appointment_cancelled").exists()


def test_cancel_outside_policy_is_409_with_reason(
    owner, patient_factory, portal_login, scheduling_deps, appointment_factory
):
    patient = patient_factory(owner.clinic)
    deps = scheduling_deps(owner.clinic, owner.user)
    appointment = appointment_factory(owner.clinic, patient, deps, hours_ahead=1)

    portal = portal_login(owner.clinic, patient)
    response = portal.post(f"/api/v1/portal/appointments/{appointment.id}/cancel/")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "policy_cutoff"
    assert "cancel" in error["message"].lower()

    appointment.refresh_from_db()
    assert appointment.status == "scheduled"


def test_reschedule_inside_policy_succeeds(
    owner, patient_factory, portal_login, scheduling_deps, appointment_factory
):
    patient = patient_factory(owner.clinic)
    deps = scheduling_deps(owner.clinic, owner.user)
    appointment = appointment_factory(owner.clinic, patient, deps, hours_ahead=72)
    new_start = timezone.now() + timedelta(hours=120)

    portal = portal_login(owner.clinic, patient)
    response = portal.post(
        f"/api/v1/portal/appointments/{appointment.id}/reschedule/",
        {"start_at": new_start.isoformat()},
    )
    assert response.status_code == 200, response.content
    assert response.json()["status"] == "scheduled"

    appointment.refresh_from_db()
    assert appointment.status == "cancelled"
    assert AuditEvent.objects.filter(action="portal.appointment_rescheduled").exists()


def test_plan_item_accept_is_visible_to_staff(owner, patient_factory, portal_login, plan_factory):
    patient = patient_factory(owner.clinic)
    plan = plan_factory(owner.clinic, patient)
    item = PlanItem.all_objects.filter(plan=plan).first()
    assert item.status == "proposed"

    portal = portal_login(owner.clinic, patient)
    response = portal.post(f"/api/v1/portal/plans/{plan.id}/items/{item.id}/accept/")
    assert response.status_code == 200, response.content
    assert response.json()["status"] == "accepted"

    staff_view = owner.get(f"/api/v1/treatment-plans/{plan.id}/")
    assert staff_view.status_code == 200
    items = {i["id"]: i["status"] for i in staff_view.json()["items"]}
    assert items[str(item.id)] == "accepted"
    assert AuditEvent.objects.filter(action="portal.plan_item_accepted").exists()


def test_plan_item_decline(owner, patient_factory, portal_login, plan_factory):
    patient = patient_factory(owner.clinic)
    plan = plan_factory(owner.clinic, patient)
    item = PlanItem.all_objects.filter(plan=plan).first()

    portal = portal_login(owner.clinic, patient)
    response = portal.post(f"/api/v1/portal/plans/{plan.id}/items/{item.id}/decline/")
    assert response.status_code == 200
    assert response.json()["status"] == "declined"
    item.refresh_from_db()
    assert item.status == "declined"


def test_document_download_is_signed_short_lived_and_audited(
    owner, patient_factory, portal_login, document_factory
):
    patient = patient_factory(owner.clinic)
    document = document_factory(owner.clinic, patient)

    portal = portal_login(owner.clinic, patient)
    listing = portal.get("/api/v1/portal/documents/")
    assert listing.status_code == 200
    result = listing.json()["results"][0]
    assert "token=" in result["download_url"]

    downloaded = portal.get(result["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.content == b"%PDF-1.4 portal test"
    assert AuditEvent.objects.filter(action="portal.document_downloaded").exists()

    # Missing or tampered token is rejected.
    assert portal.get(f"/api/v1/portal/documents/{document.id}/download/").status_code == 403
    tampered = result["download_url"].replace("token=", "token=x", 1)
    assert portal.get(tampered).status_code == 403


def test_portal_auth_actions_are_audited(owner, patient_factory, portal_login):
    patient = patient_factory(owner.clinic)
    portal_login(owner.clinic, patient)
    assert AuditEvent.objects.filter(action="portal.otp_requested").exists()
    assert AuditEvent.objects.filter(action="portal.session_created").exists()
