"""Claim validation blocks submission with an actionable error list (T134)."""

from __future__ import annotations

from datetime import date

import pytest

from apps.rcm.models import Claim
from apps.rcm.services import validation

pytestmark = pytest.mark.django_db

VALID_LINE = {"procedure_code": "D1110", "fee_minor": 5000}


def _create_claim(client, patient, *, policy=None, invoice=None, lines=None, diagnosis_codes=None):
    payload = {
        "patient": str(patient.id),
        "lines": lines if lines is not None else [dict(VALID_LINE)],
        "diagnosis_codes": ["K02.9"] if diagnosis_codes is None else diagnosis_codes,
    }
    if policy is not None:
        payload["policy"] = str(policy.id)
    if invoice is not None:
        payload["invoice"] = str(invoice.id)
    return client.post("/api/v1/claims/", payload)


def _error_codes(response):
    return {error["code"] for error in response.json()["error"]["details"]["errors"]}


def test_valid_claim_submits(owner, patient_factory, payer_factory, policy_factory):
    patient = patient_factory(owner.clinic)
    payer = payer_factory(owner.clinic)
    policy = policy_factory(owner.clinic, patient, payer)
    created = _create_claim(owner, patient, policy=policy)
    assert created.status_code == 201, created.content
    claim_id = created.json()["id"]
    assert created.json()["total_minor"] == 5000

    submitted = owner.post(f"/api/v1/claims/{claim_id}/submit/")
    assert submitted.status_code == 200, submitted.content
    assert submitted.json()["status"] == "submitted"
    assert submitted.json()["external_ref"].startswith("MOCK-")


def test_missing_diagnosis_codes_blocks_submission(owner, patient_factory, payer_factory, policy_factory):
    patient = patient_factory(owner.clinic)
    payer = payer_factory(owner.clinic)
    policy = policy_factory(owner.clinic, patient, payer)
    created = _create_claim(owner, patient, policy=policy, diagnosis_codes=[])
    claim_id = created.json()["id"]

    response = owner.post(f"/api/v1/claims/{claim_id}/submit/")
    assert response.status_code == 400, response.content
    assert "diagnosis_missing" in _error_codes(response)
    assert "diagnosis_codes" in response.json()["error"]["field_errors"]


def test_non_positive_fee_blocks_submission(owner, patient_factory, payer_factory, policy_factory):
    patient = patient_factory(owner.clinic)
    payer = payer_factory(owner.clinic)
    policy = policy_factory(owner.clinic, patient, payer)
    created = _create_claim(
        owner,
        patient,
        policy=policy,
        lines=[{"procedure_code": "D1110", "fee_minor": 0}],
    )
    response = owner.post(f"/api/v1/claims/{created.json()['id']}/submit/")
    assert response.status_code == 400
    assert "fee_not_positive" in _error_codes(response)


def test_tooth_specific_code_requires_tooth(owner, patient_factory, payer_factory, policy_factory):
    patient = patient_factory(owner.clinic)
    payer = payer_factory(owner.clinic)
    policy = policy_factory(owner.clinic, patient, payer)
    created = _create_claim(
        owner,
        patient,
        policy=policy,
        lines=[{"procedure_code": "D2391", "fee_minor": 8000}],
    )
    response = owner.post(f"/api/v1/claims/{created.json()['id']}/submit/")
    assert response.status_code == 400
    assert "tooth_required" in _error_codes(response)


def test_disallowed_procedure_blocks_submission(owner, patient_factory, payer_factory, policy_factory):
    patient = patient_factory(owner.clinic)
    payer = payer_factory(owner.clinic)
    policy = policy_factory(owner.clinic, patient, payer)
    created = _create_claim(
        owner,
        patient,
        policy=policy,
        lines=[{"procedure_code": "X9999", "fee_minor": 1000}],
    )
    response = owner.post(f"/api/v1/claims/{created.json()['id']}/submit/")
    assert response.status_code == 400
    assert "procedure_not_allowed" in _error_codes(response)


def test_expired_policy_blocks_submission(owner, patient_factory, payer_factory, policy_factory):
    patient = patient_factory(owner.clinic)
    payer = payer_factory(owner.clinic)
    policy = policy_factory(
        owner.clinic,
        patient,
        payer,
        valid_from=date(2020, 1, 1),
        valid_to=date(2020, 12, 31),
    )
    created = _create_claim(owner, patient, policy=policy)
    response = owner.post(f"/api/v1/claims/{created.json()['id']}/submit/")
    assert response.status_code == 400
    assert "policy_expired" in _error_codes(response)


def test_validation_service_returns_every_error(owner, patient_factory, payer_factory, policy_factory):
    patient = patient_factory(owner.clinic)
    payer = payer_factory(owner.clinic)
    policy = policy_factory(
        owner.clinic,
        patient,
        payer,
        valid_from=date(2020, 1, 1),
        valid_to=date(2020, 12, 31),
    )
    created = _create_claim(
        owner,
        patient,
        policy=policy,
        diagnosis_codes=[],
        lines=[{"procedure_code": "D2391", "fee_minor": 0}],
    )
    claim = Claim.all_objects.get(pk=created.json()["id"])
    errors = validation.validate(claim)
    codes = {error["code"] for error in errors}
    assert {"policy_expired", "diagnosis_missing", "fee_not_positive", "tooth_required"} <= codes
