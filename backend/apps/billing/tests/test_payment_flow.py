"""Payments, refunds and concurrency (T119)."""

from __future__ import annotations

import pytest
from django.db import connections

from apps.billing.models import Invoice, Payment

pytestmark = pytest.mark.django_db

INVOICES = "/api/v1/invoices/"


def _pay(client, invoice, amount, *, key, method="cash", **extra):
    return client.post(
        f"{INVOICES}{invoice.id}/payments/",
        {"amount_minor": amount, "method": method, **extra},
        HTTP_IDEMPOTENCY_KEY=key,
    )


def test_partial_then_full_payment_status(owner, patient_factory, invoice_factory):
    patient = patient_factory(owner.clinic)
    invoice = invoice_factory(owner.clinic, patient)
    total = invoice.total_minor

    partial = _pay(owner, invoice, total // 2, key="pay-1")
    assert partial.status_code == 201, partial.json()
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PARTIALLY_PAID
    assert invoice.paid_minor == total // 2
    assert invoice.balance_minor == total - total // 2

    remainder = total - total // 2
    full = _pay(owner, invoice, remainder, key="pay-2")
    assert full.status_code == 201, full.json()
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID
    assert invoice.balance_minor == 0
    assert full.json()["receipt_number"].startswith("RCPT-")


def test_overpayment_is_409(owner, patient_factory, invoice_factory):
    patient = patient_factory(owner.clinic)
    invoice = invoice_factory(owner.clinic, patient)
    response = _pay(owner, invoice, invoice.total_minor + 1, key="over-1")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "overpayment"
    assert Payment.all_objects.filter(invoice=invoice).count() == 0


def test_payment_requires_idempotency_key(owner, patient_factory, invoice_factory):
    patient = patient_factory(owner.clinic)
    invoice = invoice_factory(owner.clinic, patient)
    response = owner.post(
        f"{INVOICES}{invoice.id}/payments/", {"amount_minor": 100, "method": "cash"}
    )
    assert response.status_code == 400
    assert "Idempotency-Key" in response.json()["error"]["field_errors"]


def test_replayed_payment_key_does_not_double_charge(owner, patient_factory, invoice_factory):
    patient = patient_factory(owner.clinic)
    invoice = invoice_factory(owner.clinic, patient)
    first = _pay(owner, invoice, 10_000, key="replay-1")
    second = _pay(owner, invoice, 10_000, key="replay-1")
    assert first.status_code == 201
    assert second.status_code == 201
    assert second["Idempotency-Replayed"] == "true"
    assert Payment.all_objects.filter(invoice=invoice).count() == 1
    invoice.refresh_from_db()
    assert invoice.paid_minor == 10_000


def test_refund_above_captured_is_409(owner, patient_factory, invoice_factory):
    patient = patient_factory(owner.clinic)
    invoice = invoice_factory(owner.clinic, patient)
    paid = _pay(owner, invoice, 20_000, key="refund-1").json()
    response = owner.post(
        f"/api/v1/payments/{paid['id']}/refund/",
        {"amount_minor": 20_001, "reason": "too much"},
        HTTP_IDEMPOTENCY_KEY="refund-over-1",
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "refund_exceeds_captured"


def test_partial_and_full_refund_statuses(owner, patient_factory, invoice_factory):
    patient = patient_factory(owner.clinic)
    invoice = invoice_factory(owner.clinic, patient)
    paid = _pay(owner, invoice, 20_000, key="refund-flow-1").json()

    partial = owner.post(
        f"/api/v1/payments/{paid['id']}/refund/",
        {"amount_minor": 5_000, "reason": "goodwill"},
        HTTP_IDEMPOTENCY_KEY="refund-flow-2",
    )
    assert partial.status_code == 201, partial.json()
    payment = Payment.all_objects.get(pk=paid["id"])
    assert payment.status == Payment.Status.PARTIALLY_REFUNDED
    invoice.refresh_from_db()
    assert invoice.paid_minor == 15_000
    assert invoice.status == Invoice.Status.PARTIALLY_PAID

    full = owner.post(
        f"/api/v1/payments/{paid['id']}/refund/",
        {"amount_minor": 15_000, "reason": "rest"},
        HTTP_IDEMPOTENCY_KEY="refund-flow-3",
    )
    assert full.status_code == 201, full.json()
    payment.refresh_from_db()
    assert payment.status == Payment.Status.REFUNDED
    invoice.refresh_from_db()
    assert invoice.paid_minor == 0
    assert invoice.status == Invoice.Status.ISSUED


def test_dentist_cannot_record_payment(dentist, patient_factory, invoice_factory):
    patient = patient_factory(dentist.clinic)
    invoice = invoice_factory(dentist.clinic, patient)
    response = _pay(dentist, invoice, 100, key="dentist-1")
    assert response.status_code == 403


def test_payment_and_refund_write_audit_rows(owner, patient_factory, invoice_factory):
    from apps.core.models import AuditEvent

    patient = patient_factory(owner.clinic)
    invoice = invoice_factory(owner.clinic, patient)
    paid = _pay(owner, invoice, 20_000, key="audit-1").json()
    owner.post(
        f"/api/v1/payments/{paid['id']}/refund/",
        {"amount_minor": 5_000},
        HTTP_IDEMPOTENCY_KEY="audit-2",
    )
    actions = set(
        AuditEvent.objects.filter(clinic_id=owner.clinic.id).values_list("action", flat=True)
    )
    assert "invoice.created" in actions
    assert "payment.recorded" in actions
    assert "refund.recorded" in actions


def test_receptionist_can_refund(owner, staff_factory, patient_factory, invoice_factory):
    receptionist = staff_factory(clinic=owner.clinic, role="receptionist")
    patient = patient_factory(owner.clinic)
    invoice = invoice_factory(owner.clinic, patient)
    paid = _pay(owner, invoice, 10_000, key="recept-1").json()
    response = receptionist.post(
        f"/api/v1/payments/{paid['id']}/refund/",
        {"amount_minor": 1_000},
        HTTP_IDEMPOTENCY_KEY="recept-refund-1",
    )
    # Receptionist has billing.refund in the role matrix, so this must succeed.
    assert response.status_code == 201, response.json()


@pytest.mark.django_db(transaction=True)
def test_concurrent_payments_keep_a_consistent_balance(owner, patient_factory, invoice_factory):
    from concurrent.futures import ThreadPoolExecutor

    from rest_framework.test import APIClient

    patient = patient_factory(owner.clinic)
    invoice = invoice_factory(owner.clinic, patient)
    total = invoice.total_minor  # 100_000 + 18% tax
    clinic_id = str(owner.clinic.id)
    url = f"{INVOICES}{invoice.id}/payments/"

    clients = [APIClient(), APIClient()]
    for client in clients:
        client.force_login(owner.user)

    def send(client, key):
        connections.close_all()
        try:
            response = client.post(
                url,
                {"amount_minor": 60_000, "method": "cash"},
                format="json",
                HTTP_IDEMPOTENCY_KEY=key,
                HTTP_X_CLINIC_ID=clinic_id,
            )
            return response.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(send, clients[0], "conc-1")
        second = pool.submit(send, clients[1], "conc-2")
        statuses = sorted([first.result(timeout=30), second.result(timeout=30)])

    assert statuses == [201, 409], statuses
    invoice.refresh_from_db()
    assert invoice.paid_minor == 60_000
    assert invoice.balance_minor == total - 60_000
    assert Payment.all_objects.filter(invoice=invoice).count() == 1
