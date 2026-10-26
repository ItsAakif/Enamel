"""Billing models (T119).

Money is integer minor units everywhere (see ``apps.core.money``). Two hard
rules shape the schema:

1. **No card data, ever.** There is no card number, expiry, CVV or token column
   anywhere, and no serializer exposes one. The gateway adapter uses hosted
   redirect semantics, so raw card data never reaches this server.
2. **Payment state is separate from clinical state.** Billing tables reference a
   patient by id only; no clinical free text is copied into an invoice, a payment
   or a webhook payload.

``Invoice.number`` (``INV-000123``) is allocated per clinic by a locked counter
(``services.numbering``). ``WebhookEvent`` and ``ReconciliationItem`` carry a
*nullable* clinic because a gateway event can arrive before the payment (and
therefore the clinic) is known; an unmatched event lands in the reconciliation
queue instead of being dropped.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.tenancy import ClinicScopedModel


class InvoiceCounter(models.Model):
    """Per-clinic sequence row for ``Invoice.number``.

    Locked with ``SELECT ... FOR UPDATE`` while a number is handed out, so
    concurrent invoice creates in one clinic never repeat or skip a number.
    """

    clinic = models.OneToOneField(
        "clinics.Clinic",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="invoice_counter",
    )
    next_number = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "billing_invoicecounter"
        verbose_name = "Invoice counter"
        verbose_name_plural = "Invoice counters"

    def __str__(self) -> str:
        return f"{self.clinic_id}: {self.next_number}"


class Invoice(ClinicScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        PARTIALLY_PAID = "partially_paid", "Partially paid"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    # Optional link to the accepted treatment plan this invoice was raised from.
    plan = models.ForeignKey(
        "plans.TreatmentPlan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )
    number = models.CharField(max_length=16)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    currency = models.CharField(max_length=3, default="INR")
    subtotal_minor = models.BigIntegerField(default=0)
    discount_minor = models.BigIntegerField(default=0)
    tax_minor = models.BigIntegerField(default=0)
    total_minor = models.BigIntegerField(default=0)
    paid_minor = models.BigIntegerField(default=0)
    balance_minor = models.BigIntegerField(default=0)
    issued_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "billing_invoice"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "number"],
                name="uniq_invoice_clinic_number",
            ),
        ]
        indexes = [
            models.Index(fields=["clinic", "status"], name="invoice_clinic_status_idx"),
            models.Index(
                fields=["clinic", "patient"], name="invoice_clinic_patient_idx"
            ),
            models.Index(fields=["clinic", "created_at"], name="invoice_clinic_created_idx"),
        ]
        verbose_name = "Invoice"
        verbose_name_plural = "Invoices"

    def __str__(self) -> str:
        return f"{self.number} ({self.status})"


class InvoiceLine(ClinicScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    description = models.CharField(max_length=255, blank=True, default="")
    procedure_code = models.CharField(max_length=64, blank=True, default="")
    tooth = models.SmallIntegerField(null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_price_minor = models.BigIntegerField(default=0)
    discount_minor = models.BigIntegerField(default=0)
    tax_rate_bp = models.PositiveIntegerField(default=0)
    line_total_minor = models.BigIntegerField(default=0)
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "billing_invoiceline"
        ordering = ["sequence", "id"]
        indexes = [
            models.Index(fields=["clinic", "invoice"], name="inv_line_clinic_inv_idx"),
        ]
        verbose_name = "Invoice line"
        verbose_name_plural = "Invoice lines"

    def __str__(self) -> str:
        return f"{self.procedure_code} x{self.quantity}"


class Payment(ClinicScopedModel):
    """A single captured (or attempted) payment against one invoice."""

    class Method(models.TextChoices):
        CARD = "card", "Card"
        CASH = "cash", "Cash"
        UPI = "upi", "UPI"
        BANK = "bank", "Bank transfer"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"
        PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="INR")
    method = models.CharField(max_length=16, choices=Method.choices, default=Method.CASH)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.SUCCEEDED)
    gateway = models.CharField(max_length=32, blank=True, default="")
    gateway_payment_id = models.CharField(max_length=128, blank=True, default="")
    idempotency_key = models.CharField(max_length=255, blank=True, default="")
    captured_at = models.DateTimeField(null=True, blank=True)
    receipt_number = models.CharField(max_length=32, blank=True, default="")
    #: Non-sensitive gateway context only (status codes, references). Never card
    #: data: there is no field anywhere that can hold a PAN, expiry or CVV.
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "billing_payment"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uniq_payment_clinic_idempotency",
            ),
        ]
        indexes = [
            models.Index(fields=["clinic", "invoice"], name="payment_clinic_invoice_idx"),
            models.Index(fields=["clinic", "status"], name="payment_clinic_status_idx"),
            models.Index(
                fields=["gateway", "gateway_payment_id"],
                name="payment_gateway_ref_idx",
            ),
        ]
        verbose_name = "Payment"
        verbose_name_plural = "Payments"

    def __str__(self) -> str:
        return f"{self.amount_minor} {self.currency} ({self.status})"


class Refund(ClinicScopedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="refunds",
    )
    amount_minor = models.BigIntegerField()
    reason = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUCCEEDED)
    gateway_refund_id = models.CharField(max_length=128, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "billing_refund"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["clinic", "payment"], name="refund_clinic_payment_idx"),
        ]
        verbose_name = "Refund"
        verbose_name_plural = "Refunds"

    def __str__(self) -> str:
        return f"{self.amount_minor} ({self.status})"


class WebhookEvent(models.Model):
    """A de-duplicated gateway webhook.

    Not tenant-scoped: a webhook can arrive before the payment row (and hence
    the clinic) is known. ``(gateway, event_id)`` is unique, which is what makes
    replay idempotent -- a duplicate event is recorded once and never credits an
    invoice twice.
    """

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        PROCESSED = "processed", "Processed"
        DUPLICATE = "duplicate", "Duplicate"
        UNMATCHED = "unmatched", "Unmatched"
        IGNORED = "ignored", "Ignored"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    clinic = models.ForeignKey(
        "clinics.Clinic",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_events",
    )
    gateway = models.CharField(max_length=32)
    event_id = models.CharField(max_length=128)
    payload_json = models.JSONField(default=dict, blank=True)
    signature_valid = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECEIVED)
    received_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "billing_webhookevent"
        ordering = ["-received_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["gateway", "event_id"],
                name="uniq_webhook_gateway_event",
            ),
        ]
        indexes = [
            models.Index(fields=["gateway", "event_id"], name="webhook_gateway_event_idx"),
        ]
        verbose_name = "Webhook event"
        verbose_name_plural = "Webhook events"

    def __str__(self) -> str:
        return f"{self.gateway}:{self.event_id} ({self.status})"


class ReconciliationItem(models.Model):
    """A gateway event that could not be matched to a payment.

    ``clinic`` is nullable for the same reason as on ``WebhookEvent``: the
    event may be unmatched precisely because the clinic is not yet known.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    clinic = models.ForeignKey(
        "clinics.Clinic",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reconciliation_items",
    )
    event = models.ForeignKey(
        WebhookEvent,
        on_delete=models.CASCADE,
        related_name="reconciliation_items",
    )
    reason = models.CharField(max_length=64)
    amount_minor = models.BigIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    notes = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "billing_reconciliationitem"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["clinic", "status"], name="recon_clinic_status_idx"
            ),
        ]
        verbose_name = "Reconciliation item"
        verbose_name_plural = "Reconciliation items"

    def __str__(self) -> str:
        return f"{self.reason} ({self.status})"


# Stable OpenAPI enum names (SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]).
INVOICE_STATUS_CHOICES = Invoice.Status.choices
PAYMENT_METHOD_CHOICES = Payment.Method.choices
PAYMENT_STATUS_CHOICES = Payment.Status.choices
REFUND_STATUS_CHOICES = Refund.Status.choices
WEBHOOK_EVENT_STATUS_CHOICES = WebhookEvent.Status.choices
RECONCILIATION_STATUS_CHOICES = ReconciliationItem.Status.choices


__all__ = [
    "INVOICE_STATUS_CHOICES",
    "PAYMENT_METHOD_CHOICES",
    "PAYMENT_STATUS_CHOICES",
    "RECONCILIATION_STATUS_CHOICES",
    "REFUND_STATUS_CHOICES",
    "WEBHOOK_EVENT_STATUS_CHOICES",
    "Invoice",
    "InvoiceCounter",
    "InvoiceLine",
    "Payment",
    "ReconciliationItem",
    "Refund",
    "WebhookEvent",
]
