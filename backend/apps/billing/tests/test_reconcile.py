"""Reconciliation queue and receipt hashing (T119)."""

from __future__ import annotations

import json

import pytest

from apps.billing.adapters.sandbox_gateway import SandboxGateway
from apps.billing.models import ReconciliationItem
from apps.billing.services import payments as payment_service
from apps.billing.services import receipts as receipt_service

pytestmark = pytest.mark.django_db

WEBHOOK = "/api/v1/webhooks/payments/sandbox/"
RECON = "/api/v1/billing/reconciliation/"


def _post_webhook(client, payload):
    body, ts, sig = SandboxGateway().build_webhook(payload)
    return client.post(
        WEBHOOK,
        data=body,
        content_type="application/json",
        HTTP_X_PAYMENT_TIMESTAMP=ts,
        HTTP_X_PAYMENT_SIGNATURE=sig,
    )


def test_unmatched_event_creates_reconciliation_item(api_client, owner):
    response = _post_webhook(
        api_client,
        {
            "event_id": "evt-orphan-1",
            "type": "payment.succeeded",
            "gateway_payment_id": "sbx_unknown",
            "amount_minor": 12_345,
        },
    )
    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "unmatched"

    item = ReconciliationItem.objects.get(reason="unmatched_payment")
    assert item.amount_minor == 12_345
    assert item.status == ReconciliationItem.Status.OPEN

    listing = owner.get(RECON)
    assert listing.status_code == 200
    results = listing.json()["results"]
    assert any(row["reason"] == "unmatched_payment" for row in results)
    # No payload / patient data leaks through the reconciliation serializer.
    assert "payload_json" not in json.dumps(results)


def test_missing_reference_uses_a_specific_reason(api_client):
    response = _post_webhook(
        api_client, {"event_id": "evt-noref-1", "type": "payment.succeeded"}
    )
    assert response.status_code == 200
    assert response.json()["reason"] == "missing_payment_reference"
    assert ReconciliationItem.objects.filter(reason="missing_payment_reference").exists()


def test_amount_mismatch_is_flagged(api_client, owner, patient_factory, invoice_factory):
    patient = patient_factory(owner.clinic)
    invoice = invoice_factory(owner.clinic, patient)
    payment_service.record_intent(
        invoice, 20_000, gateway="sandbox", gateway_payment_id="sbx_mismatch"
    )
    response = _post_webhook(
        api_client,
        {
            "event_id": "evt-mismatch-1",
            "type": "payment.succeeded",
            "gateway_payment_id": "sbx_mismatch",
            "amount_minor": 19_000,
        },
    )
    assert response.status_code == 200, response.json()
    item = ReconciliationItem.objects.get(reason="amount_mismatch")
    assert item.clinic_id == owner.clinic.id
    listing = owner.get(RECON, {"status": "open"})
    assert listing.json()["count"] >= 1


def test_reconciliation_requires_billing_view(assistant):
    response = assistant.get(RECON)
    assert response.status_code == 403


def test_receipt_pdf_hash_is_stable(owner, patient_factory, invoice_factory):
    patient = patient_factory(owner.clinic)
    invoice = invoice_factory(owner.clinic, patient)
    payment_service.record_payment(invoice, 10_000, actor=owner.user)
    invoice.refresh_from_db()

    first = receipt_service.render_receipt_pdf(invoice)
    second = receipt_service.render_receipt_pdf(invoice)
    assert first == second
    assert receipt_service.content_hash(first) == receipt_service.content_hash(second)

    api_first = owner.get(f"/api/v1/invoices/{invoice.id}/pdf/")
    api_second = owner.get(f"/api/v1/invoices/{invoice.id}/pdf/")
    assert api_first.status_code == 200
    assert api_first["Content-Type"] == "application/pdf"
    assert api_first["X-Document-Sha256"] == api_second["X-Document-Sha256"]


def test_invoice_from_plan_endpoint(owner, patient_factory, accepted_plan):
    patient = patient_factory(owner.clinic)
    plan = accepted_plan(owner.clinic, patient, actor=owner.user)

    response = owner.post(f"/api/v1/invoices/from-plan/{plan.id}/")

    assert response.status_code == 201, response.json()
    body = response.json()
    assert [line["procedure_code"] for line in body["lines"]] == ["D2740"]
    assert body["plan"] == str(plan.id)


def test_invoice_from_plan_requires_manage(dentist, patient_factory, accepted_plan):
    patient = patient_factory(dentist.clinic)
    plan = accepted_plan(dentist.clinic, patient, actor=dentist.user)
    response = dentist.post(f"/api/v1/invoices/from-plan/{plan.id}/")
    assert response.status_code == 403
