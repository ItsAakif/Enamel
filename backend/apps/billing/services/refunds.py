"""Refunds (T119).

A refund is bounded by the *captured* amount of the payment, not the invoice
total: refunding more than was actually captured is rejected with 409
``refund_exceeds_captured``. The payment's status tracks the cumulative refunded
amount (``partially_refunded`` until the full captured amount is returned, then
``refunded``), and the invoice balance is reopened by the same amount.

The invoice is locked with ``SELECT ... FOR UPDATE`` so a refund and a concurrent
payment cannot both read the same balance.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.tenancy import with_clinic

from ..models import Invoice, Payment, Refund
from . import invoices as invoice_service

SNAPSHOT_FIELDS = ("amount_minor", "status", "reason")


def snapshot(refund: Refund) -> dict[str, Any]:
    return {name: getattr(refund, name) for name in SNAPSHOT_FIELDS}


def refunded_total(payment: Payment) -> int:
    """Return the sum of successful refunds already applied to ``payment``."""

    from django.db.models import Sum

    total = Refund.all_objects.filter(
        payment_id=payment.pk, status=Refund.Status.SUCCEEDED
    ).aggregate(total=Sum("amount_minor"))["total"]
    return int(total or 0)


def refund_payment(
    payment: Payment,
    amount_minor: int,
    *,
    reason: str = "",
    gateway_refund_id: str = "",
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> Refund:
    """Refund part or all of a captured payment."""

    if amount_minor <= 0:
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={"amount_minor": ["must be greater than zero"]},
        )

    with transaction.atomic():
        with with_clinic(payment.clinic):
            invoice = Invoice.all_objects.select_for_update().get(pk=payment.invoice_id)
            locked_payment = Payment.all_objects.select_for_update().get(pk=payment.pk)

            if locked_payment.status not in (
                Payment.Status.SUCCEEDED,
                Payment.Status.PARTIALLY_REFUNDED,
            ):
                raise ApiError(
                    "conflict",
                    "Only a captured payment can be refunded.",
                    status_code=409,
                    details={"status": locked_payment.status},
                )
            if locked_payment.status == Payment.Status.PENDING:
                raise ApiError(
                    "conflict",
                    "A pending payment cannot be refunded.",
                    status_code=409,
                    details={"status": locked_payment.status},
                )

            already = refunded_total(locked_payment)
            captured = locked_payment.amount_minor
            if already + amount_minor > captured:
                raise ApiError(
                    "refund_exceeds_captured",
                    "The refund exceeds the captured amount.",
                    status_code=409,
                    details={
                        "captured_minor": captured,
                        "already_refunded_minor": already,
                        "amount_minor": amount_minor,
                    },
                )

            refund = Refund.all_objects.create(
                clinic_id=locked_payment.clinic_id,
                payment=locked_payment,
                amount_minor=amount_minor,
                reason=reason,
                status=Refund.Status.SUCCEEDED,
                gateway_refund_id=gateway_refund_id,
                created_by=actor if getattr(actor, "pk", None) else None,
            )

            total_refunded = already + amount_minor
            locked_payment.status = (
                Payment.Status.REFUNDED
                if total_refunded >= captured
                else Payment.Status.PARTIALLY_REFUNDED
            )
            locked_payment.save(update_fields=["status", "updated_at"])

            invoice.paid_minor = invoice.paid_minor - amount_minor
            invoice.balance_minor = invoice.total_minor - invoice.paid_minor
            if invoice.balance_minor <= 0:
                invoice.status = Invoice.Status.PAID
            elif invoice.paid_minor <= 0:
                invoice.status = Invoice.Status.ISSUED
            else:
                invoice.status = Invoice.Status.PARTIALLY_PAID
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

            record_audit(
                "refund.recorded",
                entity=refund,
                after=snapshot(refund),
                actor=actor,
                request=request,
            )
            record_audit(
                "invoice.refund_applied",
                entity=invoice,
                after=invoice_service.snapshot(invoice),
                actor=actor,
                request=request,
            )
    return refund


__all__ = ["SNAPSHOT_FIELDS", "refund_payment", "refunded_total", "snapshot"]
