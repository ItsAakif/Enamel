"""RCM serializers (T134).

Every write serializer validates referenced objects inside the request clinic so
a foreign id is a field error, never a cross-tenant read. Monetary values are
integer minor units.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.billing.models import Invoice
from apps.patients.models import Patient

from .models import (
    Claim,
    ClaimEvent,
    ClaimLine,
    EligibilityCheck,
    InsurancePolicy,
    Payer,
    Remittance,
)


def _clinic(context) -> object:
    clinic = context.get("clinic")
    if clinic is None:
        raise serializers.ValidationError("A clinic scope is required.")
    return clinic


class PayerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payer
        fields = ["id", "name", "code", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class InsurancePolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = InsurancePolicy
        fields = [
            "id",
            "patient",
            "payer",
            "member_id",
            "plan_name",
            "valid_from",
            "valid_to",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_patient(self, value):
        clinic = _clinic(self.context)
        if not Patient.all_objects.filter(clinic=clinic, pk=value.pk).exists():
            raise serializers.ValidationError("patient not found in this clinic")
        return value

    def validate_payer(self, value):
        clinic = _clinic(self.context)
        if not Payer.all_objects.filter(clinic=clinic, pk=value.pk).exists():
            raise serializers.ValidationError("payer not found in this clinic")
        return value

    def validate(self, attrs):
        valid_from = attrs.get("valid_from", getattr(self.instance, "valid_from", None))
        valid_to = attrs.get("valid_to", getattr(self.instance, "valid_to", None))
        if valid_from and valid_to and valid_to < valid_from:
            raise serializers.ValidationError(
                {"valid_to": ["must not be before valid_from"]}
            )
        return attrs


class EligibilityCheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = EligibilityCheck
        fields = [
            "id",
            "policy",
            "service_date",
            "status",
            "response_json",
            "error_code",
            "checked_at",
        ]
        read_only_fields = fields


class EligibilityCheckCreateSerializer(serializers.Serializer):
    policy = serializers.UUIDField()
    service_date = serializers.DateField()

    def validate_policy(self, value):
        clinic = _clinic(self.context)
        if not InsurancePolicy.all_objects.filter(clinic=clinic, pk=value).exists():
            raise serializers.ValidationError("policy not found in this clinic")
        return value


class ClaimLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClaimLine
        fields = ["id", "procedure_code", "tooth", "fee_minor", "units", "sequence"]
        read_only_fields = fields


class ClaimLineInputSerializer(serializers.Serializer):
    procedure_code = serializers.CharField(max_length=32)
    tooth = serializers.IntegerField(required=False, allow_null=True)
    fee_minor = serializers.IntegerField()
    units = serializers.IntegerField(required=False, min_value=1, default=1)
    sequence = serializers.IntegerField(required=False, min_value=0)


class ClaimSerializer(serializers.ModelSerializer):
    lines = ClaimLineSerializer(many=True, read_only=True)

    class Meta:
        model = Claim
        fields = [
            "id",
            "patient",
            "invoice",
            "policy",
            "payer",
            "status",
            "diagnosis_codes",
            "total_minor",
            "external_ref",
            "submitted_at",
            "correction_of",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "total_minor",
            "external_ref",
            "submitted_at",
            "lines",
            "created_at",
            "updated_at",
        ]


class ClaimCreateSerializer(serializers.Serializer):
    patient = serializers.UUIDField()
    invoice = serializers.UUIDField(required=False, allow_null=True)
    policy = serializers.UUIDField(required=False, allow_null=True)
    payer = serializers.UUIDField(required=False, allow_null=True)
    diagnosis_codes = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    lines = ClaimLineInputSerializer(many=True)

    def validate_patient(self, value):
        clinic = _clinic(self.context)
        if not Patient.all_objects.filter(clinic=clinic, pk=value).exists():
            raise serializers.ValidationError("patient not found in this clinic")
        return value

    def validate_invoice(self, value):
        if value is None:
            return value
        clinic = _clinic(self.context)
        if not Invoice.all_objects.filter(clinic=clinic, pk=value).exists():
            raise serializers.ValidationError("invoice not found in this clinic")
        return value

    def validate_policy(self, value):
        if value is None:
            return value
        clinic = _clinic(self.context)
        if not InsurancePolicy.all_objects.filter(clinic=clinic, pk=value).exists():
            raise serializers.ValidationError("policy not found in this clinic")
        return value

    def validate_payer(self, value):
        if value is None:
            return value
        clinic = _clinic(self.context)
        if not Payer.all_objects.filter(clinic=clinic, pk=value).exists():
            raise serializers.ValidationError("payer not found in this clinic")
        return value

    def validate_lines(self, value):
        if not value:
            raise serializers.ValidationError("at least one claim line is required")
        return value


class ClaimEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClaimEvent
        fields = ["id", "claim", "event_type", "payload_json", "at"]
        read_only_fields = fields


class RemittanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Remittance
        fields = [
            "id",
            "claim",
            "payer_ref",
            "paid_minor",
            "adjustment_minor",
            "adjustments_json",
            "status",
            "posted_at",
        ]
        read_only_fields = fields


class AdjustmentInputSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    amount_minor = serializers.IntegerField()
    description = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )


class RemittanceCreateSerializer(serializers.Serializer):
    claim = serializers.UUIDField()
    payer_ref = serializers.CharField(max_length=128)
    paid_minor = serializers.IntegerField(required=False, min_value=0, default=0)
    adjustments = AdjustmentInputSerializer(many=True, required=False, default=list)
    status = serializers.ChoiceField(
        choices=Remittance.Status.choices, required=False, default=Remittance.Status.POSTED
    )

    def validate_claim(self, value):
        clinic = _clinic(self.context)
        if not Claim.all_objects.filter(clinic=clinic, pk=value).exists():
            raise serializers.ValidationError("claim not found in this clinic")
        return value


class ReconciliationSerializer(serializers.Serializer):
    claims = serializers.ListField(child=serializers.DictField())
    summary = serializers.DictField()
    zero_drift = serializers.BooleanField()


__all__ = [
    "AdjustmentInputSerializer",
    "ClaimCreateSerializer",
    "ClaimEventSerializer",
    "ClaimLineInputSerializer",
    "ClaimLineSerializer",
    "ClaimSerializer",
    "EligibilityCheckCreateSerializer",
    "EligibilityCheckSerializer",
    "InsurancePolicySerializer",
    "PayerSerializer",
    "ReconciliationSerializer",
    "RemittanceCreateSerializer",
    "RemittanceSerializer",
]
