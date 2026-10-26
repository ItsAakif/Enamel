"""Claim submission idempotency, denial/correction and the feature flag (T134)."""

from __future__ import annotations

import uuid

import pytest

from apps.rcm.models import Claim, ClaimEvent, ClaimLine

pytestmark = pytest.mark.django_db


def _create_submittable_claim(owner, patient_factory, payer_factory, policy_factory):
    patient = patient_factory(owner.clinic)
    payer = payer_factory(owner.clinic)
    policy = policy_factory(owner.clinic, patient, payer)
    created = owner.post(
        "/api/v1/claims/",
        {
            "patient": str(patient.id),
            "policy": str(policy.id),
            "diagnosis_codes": ["K02.9"],
            "lines": [{"procedure_code": "D1110", "fee_minor": 5000}],
        },
    )
    assert created.status_code == 201, created.content
    return created.json()["id"]


def test_submission_is_idempotent(owner, patient_factory, payer_factory, policy_factory):
    claim_id = _create_submittable_claim(owner, patient_factory, payer_factory, policy_factory)

    first = owner.post(f"/api/v1/claims/{claim_id}/submit/")
    assert first.status_code == 200, first.content
    external_ref = first.json()["external_ref"]
    assert external_ref

    second = owner.post(f"/api/v1/claims/{claim_id}/submit/")
    assert second.status_code == 200, second.content
    assert second.json()["external_ref"] == external_ref
    assert (
        ClaimEvent.all_objects.filter(
            claim_id=claim_id, event_type=ClaimEvent.EventType.SUBMITTED
        ).count()
        == 1
    )


def test_claim_events_are_listed(owner, patient_factory, payer_factory, policy_factory):
    claim_id = _create_submittable_claim(owner, patient_factory, payer_factory, policy_factory)
    owner.post(f"/api/v1/claims/{claim_id}/submit/")

    response = owner.get(f"/api/v1/claims/{claim_id}/events/")
    assert response.status_code == 200, response.content
    event_types = [row["event_type"] for row in response.json()["results"]]
    assert {"created", "validated", "submitted"} <= set(event_types)


def test_denial_creates_a_correction_and_keeps_the_original(
    owner, patient_factory, payer_factory, policy_factory
):
    claim_id = _create_submittable_claim(owner, patient_factory, payer_factory, policy_factory)
    owner.post(f"/api/v1/claims/{claim_id}/submit/")

    original = Claim.all_objects.get(pk=claim_id)
    original_total = original.total_minor
    original_lines = list(
        ClaimLine.all_objects.filter(claim_id=claim_id).values_list("procedure_code", "fee_minor")
    )

    response = owner.post(
        "/api/v1/remittances/",
        {"claim": claim_id, "payer_ref": "DENY-1", "paid_minor": 0, "status": "denied"},
    )
    assert response.status_code == 201, response.content

    original.refresh_from_db()
    assert original.status == Claim.Status.DENIED
    # The original's content is untouched.
    assert original.total_minor == original_total
    assert list(
        ClaimLine.all_objects.filter(claim_id=claim_id).values_list("procedure_code", "fee_minor")
    ) == original_lines

    correction = Claim.all_objects.filter(correction_of_id=claim_id).first()
    assert correction is not None
    assert correction.status == Claim.Status.DRAFT
    assert correction.total_minor == original_total
    assert ClaimLine.all_objects.filter(claim_id=correction.pk).count() == len(original_lines)


def test_cross_clinic_claim_is_404(owner, staff_factory, patient_factory, payer_factory, policy_factory):
    other = staff_factory(role="owner")
    other_claim = _create_submittable_claim(other, patient_factory, payer_factory, policy_factory)
    assert owner.get(f"/api/v1/claims/{other_claim}/events/").status_code == 404
    assert owner.post(f"/api/v1/claims/{other_claim}/submit/").status_code == 404


def test_disabled_flag_returns_404_on_every_endpoint(settings, owner, api_client):
    settings.RCM_ENABLED = False
    claim_id = str(uuid.uuid4())

    checks = [
        ("get", "/api/v1/payers/", None),
        ("post", "/api/v1/payers/", {"name": "X", "code": "X"}),
        ("get", "/api/v1/insurance-policies/", None),
        ("post", "/api/v1/insurance-policies/", {}),
        ("post", "/api/v1/eligibility-checks/", {"policy": claim_id, "service_date": "2026-06-01"}),
        ("get", "/api/v1/claims/", None),
        ("post", "/api/v1/claims/", {"patient": claim_id, "lines": []}),
        ("post", f"/api/v1/claims/{claim_id}/submit/", None),
        ("get", f"/api/v1/claims/{claim_id}/events/", None),
        ("post", "/api/v1/remittances/", {"claim": claim_id, "payer_ref": "X"}),
        ("get", "/api/v1/rcm/reconciliation/", None),
    ]
    for method, url, payload in checks:
        response = getattr(owner, method)(url, payload) if payload is not None else getattr(owner, method)(url)
        assert response.status_code == 404, f"{method} {url} -> {response.status_code}"
        assert response.json()["error"]["code"] == "not_found"

    # Disabled is 404 even for an anonymous caller (before authentication).
    assert api_client.get("/api/v1/payers/").status_code == 404
