"""Payment webhook signature and replay handling (T119)."""

from __future__ import annotations

import json

import pytest

from apps.billing.adapters.sandbox_gateway import SandboxGateway
from apps.billing.models import Invoice, WebhookEvent
from apps.billing.services import payments as payment_service

pytestmark = pytest.mark.django_db

WEBHOOK = "/api/v1/webhooks/payments/sandbox/"


def _post_webhook(client, payload, *, timestamp=None, signature=None):
    gateway = SandboxGateway()
    body, ts, sig = gateway.build_webhook(payload, timestamp=timestamp)
    return client.post(
        WEBHOOK,
        data=body,
        content_type="application/json",
        HTTP_X_PAYMENT_TIMESTAMP=ts if timestamp is None else timestamp,
        HTTP_X_PAYMENT_SIGNATURE=sig if signature is None else signature,
    )


def test_invalid_signature_is_400_and_not_stored(api_client):
    payload = {"event_id": "evt-1", "type": "payment.succeeded", "gateway_payment_id": "x"}
    response = api_client.post(
        WEBHOOK,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_PAYMENT_TIMESTAMP="1700000000",
        HTTP_X_PAYMENT_SIGNATURE="deadbeef",
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_signature"
    assert WebhookEvent.objects.count() == 0


def test_stale_timestamp_is_rejected(api_client):
    payload = {"event_id": "evt-stale", "type": "payment.succeeded", "gateway_payment_id": "x"}
    response = _post_webhook(api_client, payload, timestamp="1")
    assert response.status_code == 400
    assert WebhookEvent.objects.count() == 0


def test_unknown_gateway_is_404(api_client):
    response = api_client.post(
        "/api/v1/webhooks/payments/nope/",
        data=b"{}",
        content_type="application/json",
        HTTP_X_PAYMENT_TIMESTAMP="1700000000",
        HTTP_X_PAYMENT_SIGNATURE="x",
    )
    assert response.status_code == 404


def test_duplicate_event_does_not_double_credit(api_client, owner, patient_factory, invoice_factory):
    patient = patient_factory(owner.clinic)
    invoice = invoice_factory(owner.clinic, patient)
    pending = payment_service.record_intent(
        invoice, 50_000, gateway="sandbox", gateway_payment_id="sbx_dupe"
    )
    payload = {
        "event_id": "evt-dupe-1",
        "type": "payment.succeeded",
        "gateway_payment_id": "sbx_dupe",
        "amount_minor": 50_000,
    }

    first = _post_webhook(api_client, payload)
    assert first.status_code == 200, first.json()
    assert first.json()["status"] == "processed"

    invoice.refresh_from_db()
    assert invoice.paid_minor == 50_000

    second = _post_webhook(api_client, payload)
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

    invoice.refresh_from_db()
    assert invoice.paid_minor == 50_000  # credited exactly once
    pending.refresh_from_db()
    assert pending.status == "succeeded"
    assert WebhookEvent.objects.count() == 1


def test_sandbox_gateway_is_deterministic_and_offline():
    from apps.billing.adapters.sandbox_gateway import SandboxGateway

    gateway = SandboxGateway()
    first = gateway.create_intent(
        invoice_number="INV-000001", amount_minor=10_000, currency="INR"
    )
    second = gateway.create_intent(
        invoice_number="INV-000001", amount_minor=10_000, currency="INR"
    )
    assert first.gateway_payment_id == second.gateway_payment_id
    assert first.gateway_payment_id.startswith("sbx_")
    assert "sandbox.enamel.local" in first.redirect_url

    refund_id = gateway.refund(
        gateway_payment_id=first.gateway_payment_id, amount_minor=1_000, currency="INR"
    )
    assert refund_id.startswith("sbxr_")

    body, ts, sig = gateway.build_webhook({"event_id": "evt-x", "type": "payment.succeeded"})
    assert gateway.verify_webhook(timestamp=ts, body=body, signature=sig) is True
    assert gateway.verify_webhook(timestamp=ts, body=body, signature="bad") is False


def test_failed_event_marks_payment_failed(api_client, owner, patient_factory, invoice_factory):
    patient = patient_factory(owner.clinic)
    invoice = invoice_factory(owner.clinic, patient)
    pending = payment_service.record_intent(
        invoice, 50_000, gateway="sandbox", gateway_payment_id="sbx_fail"
    )
    response = _post_webhook(
        api_client,
        {
            "event_id": "evt-fail-1",
            "type": "payment.failed",
            "gateway_payment_id": "sbx_fail",
        },
    )
    assert response.status_code == 200, response.json()
    pending.refresh_from_db()
    assert pending.status == "failed"
    invoice.refresh_from_db()
    assert invoice.paid_minor == 0
    assert invoice.status == Invoice.Status.ISSUED
