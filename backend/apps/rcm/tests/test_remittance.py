"""Remittance posting, reconciliation and duplicate rejection (T134)."""

from __future__ import annotations

import pytest

from apps.billing.models import Payment
from apps.rcm.models import Claim, Remittance

pytestmark = pytest.mark.django_db


def _make_claim(owner, patient_factory, payer_factory, policy_factory, invoice_factory, *, total=5000):
    patient = patient_factory(owner.clinic)
    payer = payer_factory(owner.clinic)
    policy = policy_factory(owner.clinic, patient, payer)
    invoice = invoice_factory(owner.clinic, patient, total_minor=total)
    created = owner.post(
        "/api/v1/claims/",
        {
            "patient": str(patient.id),
            "policy": str(policy.id),
            "invoice": str(invoice.id),
            "diagnosis_codes": ["K02.9"],
            "lines": [{"procedure_code": "D1110", "fee_minor": total}],
        },
    )
    assert created.status_code == 201, created.content
    claim_id = created.json()["id"]
    owner.post(f"/api/v1/claims/{claim_id}/submit/")
    return claim_id, invoice


def test_remittance_posts_paid_and_itemized_adjustments(
    owner, patient_factory, payer_factory, policy_factory, invoice_factory
):
    claim_id, invoice = _make_claim(
        owner, patient_factory, payer_factory, policy_factory, invoice_factory
    )

    response = owner.post(
        "/api/v1/remittances/",
        {
            "claim": claim_id,
            "payer_ref": "REM-100",
            "paid_minor": 4000,
            "adjustments": [
                {"code": "CO-45", "amount_minor": 1000, "description": "contractual"}
            ],
        },
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["paid_minor"] == 4000
    assert body["adjustment_minor"] == 1000
    assert body["adjustments_json"][0]["code"] == "CO-45"

    invoice.refresh_from_db()
    assert invoice.paid_minor == 4000
    assert invoice.balance_minor == 1000
    assert Payment.all_objects.filter(invoice_id=invoice.id).count() == 1

    claim = Claim.all_objects.get(pk=claim_id)
    assert claim.status == Claim.Status.PAID

    report = owner.get("/api/v1/rcm/reconciliation/").json()
    assert report["zero_drift"] is True
    row = next(r for r in report["claims"] if r["claim_id"] == claim_id)
    assert row["paid_minor"] == 4000
    assert row["adjustment_minor"] == 1000
    assert row["outstanding_minor"] == 0
    assert row["drift_minor"] == 0
    assert row["invoice_balance_minor"] == 1000


def test_partial_remittance_leaves_the_claim_accepted(
    owner, patient_factory, payer_factory, policy_factory, invoice_factory
):
    claim_id, _invoice = _make_claim(
        owner, patient_factory, payer_factory, policy_factory, invoice_factory
    )
    owner.post(
        "/api/v1/remittances/",
        {"claim": claim_id, "payer_ref": "REM-PART", "paid_minor": 1000},
    )
    claim = Claim.all_objects.get(pk=claim_id)
    assert claim.status == Claim.Status.ACCEPTED

    report = owner.get("/api/v1/rcm/reconciliation/").json()
    row = next(r for r in report["claims"] if r["claim_id"] == claim_id)
    assert row["outstanding_minor"] == 4000
    assert row["drift_minor"] is None  # not settled yet


def test_duplicate_payer_ref_is_rejected_without_double_posting(
    owner, patient_factory, payer_factory, policy_factory, invoice_factory
):
    claim_id, invoice = _make_claim(
        owner, patient_factory, payer_factory, policy_factory, invoice_factory
    )
    first = owner.post(
        "/api/v1/remittances/",
        {"claim": claim_id, "payer_ref": "REM-DUP", "paid_minor": 2000},
    )
    assert first.status_code == 201, first.content

    duplicate = owner.post(
        "/api/v1/remittances/",
        {"claim": claim_id, "payer_ref": "REM-DUP", "paid_minor": 2000},
    )
    assert duplicate.status_code == 409, duplicate.content
    assert duplicate.json()["error"]["code"] == "conflict"

    invoice.refresh_from_db()
    assert invoice.paid_minor == 2000
    assert invoice.balance_minor == 3000
    assert Remittance.all_objects.filter(payer_ref="REM-DUP").count() == 1
    assert Payment.all_objects.filter(invoice_id=invoice.id).count() == 1


def test_remittance_requires_manage(assistant, owner, patient_factory, payer_factory, policy_factory, invoice_factory):
    claim_id, _invoice = _make_claim(
        owner, patient_factory, payer_factory, policy_factory, invoice_factory
    )
    response = assistant.post(
        "/api/v1/remittances/",
        {"claim": claim_id, "payer_ref": "REM-NO", "paid_minor": 100},
    )
    assert response.status_code == 403
