"""Billing serializers (T119).

No serializer exposes a card field, because no model has one: payments are
recorded by method and gateway reference only. Money is integer minor units
throughout and every write serializer validates its patient inside the request
clinic, so a foreign id is a field error rather than a cross-tenant read.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.patients.models import Patient
from apps.plans.models import TreatmentPlan

from .models import Invoice, InvoiceLine, Payment, ReconciliationItem, Refund


def _clinic(context) -> object:
    clinic = context.get("clinic")
    if clinic is None:
        raise serializers.ValidationError("A clinic scope is required.")
    return clinic


class InvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = [
            "id",
            "invoice",
            "description",
            "procedure_code",
            "tooth",
            "quantity",
            "unit_price_minor",
            "discount_minor",
            "tax_rate_bp",
            "line_total_minor",
            "sequence",
        ]
        read_only_fields = fields


class InvoiceLineWriteSerializer(serializers.Serializer):
    description = serializers.CharField(required=False, allow_blank=True, default="")
    procedure_code = serializers.CharField(required=False, allow_blank=True, default="")
    tooth = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=99)
    quantity = serializers.IntegerField(required=False, min_value=1, default=1)
    unit_price_minor = serializers.IntegerField(required=False, min_value=0, default=0)
    discount_minor = serializers.IntegerField(required=False, min_value=0, default=0)
    tax_rate_bp = serializers.IntegerField(required=False, min_value=0, default=0)


class InvoiceSerializer(serializers.ModelSerializer):
    lines = InvoiceLineSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "patient",
            "plan",
            "number",
            "status",
            "currency",
            "subtotal_minor",
            "discount_minor",
            "tax_minor",
            "total_minor",
            "paid_minor",
            "balance_minor",
            "issued_at",
            "due_at",
            "notes",
            "version",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class InvoiceCreateSerializer(serializers.Serializer):
    patient = serializers.UUIDField()
    plan = serializers.UUIDField(required=False, allow_null=True)
    currency = serializers.CharField(required=False, allow_blank=True, max_length=3)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    due_at = serializers.DateTimeField(required=False, allow_null=True)
    lines = InvoiceLineWriteSerializer(many=True, required=False, default=list)

    def validate_patient(self, value):
        if not Patient.all_objects.filter(
            pk=value, clinic=_clinic(self.context), deleted_at__isnull=True
        ).exists():
            raise serializers.ValidationError("patient not found in this clinic")
        return value

    def validate_plan(self, value):
        if value is None:
            return value
        if not TreatmentPlan.all_objects.filter(
            pk=value, clinic=_clinic(self.context)
        ).exists():
            raise serializers.ValidationError("plan not found in this clinic")
        return value


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "id",
            "invoice",
            "amount_minor",
            "currency",
            "method",
            "status",
            "gateway",
            "gateway_payment_id",
            "receipt_number",
            "captured_at",
            "created_at",
        ]
        read_only_fields = fields


class PaymentCreateSerializer(serializers.Serializer):
    amount_minor = serializers.IntegerField(min_value=1)
    method = serializers.ChoiceField(choices=Payment.Method.choices, default=Payment.Method.CASH)
    gateway = serializers.CharField(required=False, allow_blank=True, default="", max_length=32)
    gateway_payment_id = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=128
    )
    metadata = serializers.DictField(required=False, default=dict)


class RefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = [
            "id",
            "payment",
            "amount_minor",
            "reason",
            "status",
            "gateway_refund_id",
            "created_at",
        ]
        read_only_fields = fields


class RefundCreateSerializer(serializers.Serializer):
    amount_minor = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)


class ReconciliationItemSerializer(serializers.ModelSerializer):
    gateway = serializers.CharField(source="event.gateway", read_only=True)
    event_id = serializers.CharField(source="event.event_id", read_only=True)

    class Meta:
        model = ReconciliationItem
        fields = [
            "id",
            "clinic",
            "event",
            "event_id",
            "gateway",
            "reason",
            "amount_minor",
            "status",
            "notes",
            "created_at",
        ]
        read_only_fields = fields


__all__ = [
    "InvoiceCreateSerializer",
    "InvoiceLineSerializer",
    "InvoiceLineWriteSerializer",
    "InvoiceSerializer",
    "PaymentCreateSerializer",
    "PaymentSerializer",
    "ReconciliationItemSerializer",
    "RefundCreateSerializer",
    "RefundSerializer",
]
