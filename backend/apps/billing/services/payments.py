"""Payment recording (T119).

Recording a payment locks the invoice with ``SELECT ... FOR UPDATE`` before it
reads the balance, so two concurrent payments serialise and the second sees the
first's effect: the balance can never go negative and no update is lost. A
payment larger than the outstanding balance is rejected with 409 ``overpayment``.

The row itself is the immutable payment event; every mutation also writes an
append-only audit row (``payment.recorded`` / ``invoice.payment_applied``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.tenancy import with_clinic

from ..models import Invoice, Payment
from . import invoices as invoice_service
from .numbering import format_receipt_number

SNAPSHOT_FIELDS = ("amount_minor", "currency", "method", "status", "receipt_number")


def snapshot(payment: Payment) -> dict[str, Any]:
    """Audit projection for a payment (no gateway secrets, no card data)."""

    return {name: getattr(payment, name) for name in SNAPSHOT_FIELDS}


def _next_receipt_sequence(invoice: Invoice) -> int:
    return Payment.all_objects.filter(invoice_id=invoice.pk).count() + 1


def record_payment(
    invoice: Invoice,
    amount_minor: int,
    *,
    method: str = Payment.Method.CASH,
    gateway: str = "",
    gateway_payment_id: str = "",
    idempotency_key: str = "",
    metadata: dict[str, Any] | None = None,
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> Payment:
    """Record a captured payment against ``invoice`` (row-locked)."""

    if amount_minor <= 0:
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={"amount_minor": ["must be greater than zero"]},
        )

    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(invoice.clinic):
            locked = Invoice.all_objects.select_for_update().get(pk=invoice.pk)
            if locked.status == Invoice.Status.VOID:
                raise ApiError(
                    "conflict",
                    "This invoice is void and cannot be paid.",
                    status_code=409,
                    details={"status": locked.status},
                )
            if amount_minor > locked.balance_minor:
                raise ApiError(
                    "overpayment",
                    "The payment exceeds the outstanding balance.",
                    status_code=409,
                    details={
                        "balance_minor": locked.balance_minor,
                        "amount_minor": amount_minor,
                    },
                )

            payment = Payment.all_objects.create(
                clinic_id=locked.clinic_id,
                invoice=locked,
                amount_minor=amount_minor,
                currency=locked.currency,
                method=method,
                status=Payment.Status.SUCCEEDED,
                gateway=gateway,
                gateway_payment_id=gateway_payment_id,
                idempotency_key=idempotency_key,
                captured_at=moment,
                receipt_number=format_receipt_number(locked, _next_receipt_sequence(locked)),
                metadata=metadata or {},
            )
            _apply_credit(locked, amount_minor)
            record_audit(
                "payment.recorded",
                entity=payment,
                after=snapshot(payment),
                actor=actor,
                request=request,
            )
            record_audit(
                "invoice.payment_applied",
                entity=locked,
                after=invoice_service.snapshot(locked),
                actor=actor,
                request=request,
            )
    return payment


def record_intent(
    invoice: Invoice,
    amount_minor: int,
    *,
    gateway: str,
    gateway_payment_id: str = "",
    method: str = Payment.Method.CARD,
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> Payment:
    """Create a *pending* gateway payment without crediting the invoice.

    The invoice is credited only when the gateway confirms the payment through a
    signed webhook (``services.reconcile``). Creating an intent never touches the
    balance, so a failed or abandoned redirect leaves no money on the books.
    """

    if amount_minor <= 0:
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={"amount_minor": ["must be greater than zero"]},
        )
    with transaction.atomic():
        with with_clinic(invoice.clinic):
            locked = Invoice.all_objects.select_for_update().get(pk=invoice.pk)
            if amount_minor > locked.balance_minor:
                raise ApiError(
                    "overpayment",
                    "The payment exceeds the outstanding balance.",
                    status_code=409,
                    details={
                        "balance_minor": locked.balance_minor,
                        "amount_minor": amount_minor,
                    },
                )
            return Payment.all_objects.create(
                clinic_id=locked.clinic_id,
                invoice=locked,
                amount_minor=amount_minor,
                currency=locked.currency,
                method=method,
                status=Payment.Status.PENDING,
                gateway=gateway,
                gateway_payment_id=gateway_payment_id,
                captured_at=None,
                metadata={},
            )


def _apply_credit(invoice: Invoice, amount_minor: int) -> None:
    """Apply a credit to a locked invoice and persist status/balance."""

    invoice.paid_minor = invoice.paid_minor + amount_minor
    invoice.balance_minor = invoice.total_minor - invoice.paid_minor
    invoice.status = (
        Invoice.Status.PAID
        if invoice.balance_minor <= 0
        else Invoice.Status.PARTIALLY_PAID
    )
    invoice.version = invoice.version + 1
    invoice.save(
        update_fields=[
            "paid_minor",
            "balance_minor",
            "status",
            "version",
            "updated_at",
        ]
    )


def credit_pending_payment(
    payment: Payment,
    *,
    gateway_payment_id: str = "",
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> Payment:
    """Mark a pending gateway payment succeeded and credit its invoice once."""

    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(payment.clinic):
            locked_payment = Payment.all_objects.select_for_update().get(pk=payment.pk)
            if locked_payment.status == Payment.Status.SUCCEEDED:
                # Already credited: a replayed confirmation is a no-op.
                return locked_payment
            invoice = Invoice.all_objects.select_for_update().get(
                pk=locked_payment.invoice_id
            )
            locked_payment.status = Payment.Status.SUCCEEDED
            locked_payment.captured_at = moment
            if gateway_payment_id:
                locked_payment.gateway_payment_id = gateway_payment_id
            if not locked_payment.receipt_number:
                locked_payment.receipt_number = format_receipt_number(
                    invoice, _next_receipt_sequence(invoice)
                )
            locked_payment.save(
                update_fields=[
                    "status",
                    "captured_at",
                    "gateway_payment_id",
                    "receipt_number",
                    "updated_at",
                ]
            )
            _apply_credit(invoice, locked_payment.amount_minor)
            record_audit(
                "payment.recorded",
                entity=locked_payment,
                after=snapshot(locked_payment),
                actor=actor,
                request=request,
            )
            record_audit(
                "invoice.payment_applied",
                entity=invoice,
                after=invoice_service.snapshot(invoice),
                actor=actor,
                request=request,
            )
    return locked_payment


def mark_failed(
    payment: Payment,
    *,
    actor: Any = None,
    request: Any = None,
) -> Payment:
    """Mark a pending gateway payment failed (no balance change)."""

    with transaction.atomic():
        with with_clinic(payment.clinic):
            locked_payment = Payment.all_objects.select_for_update().get(pk=payment.pk)
            if locked_payment.status in (
                Payment.Status.SUCCEEDED,
                Payment.Status.REFUNDED,
                Payment.Status.PARTIALLY_REFUNDED,
            ):
                return locked_payment
            locked_payment.status = Payment.Status.FAILED
            locked_payment.save(update_fields=["status", "updated_at"])
            record_audit(
                "payment.failed",
                entity=locked_payment,
                after=snapshot(locked_payment),
                actor=actor,
                request=request,
            )
    return locked_payment


__all__ = [
    "SNAPSHOT_FIELDS",
    "credit_pending_payment",
    "mark_failed",
    "record_intent",
    "record_payment",
    "snapshot",
]
