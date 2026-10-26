"""Plans serializers (T117).

Read serializers expose money as integer minor units and never a raw IP hash or
user agent. Write serializers validate every referenced object *inside the
request clinic*, so a foreign id is a field error rather than a cross-tenant
read. Client-supplied totals are not part of any write serializer: the server
recomputes them.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.clinics.models import Membership
from apps.patients.models import Patient

from .models import (
    ConsentRecord,
    ConsentTemplate,
    Document,
    PlanItem,
    TreatmentPlan,
)
from .services import documents as document_service
from .services import signature as signature_service


def _clinic(context) -> object:
    clinic = context.get("clinic")
    if clinic is None:
        raise serializers.ValidationError("A clinic scope is required.")
    return clinic


def _patient_exists(clinic, value) -> bool:
    return Patient.all_objects.filter(
        pk=value, clinic=clinic, deleted_at__isnull=True
    ).exists()


class PlanItemSerializer(serializers.ModelSerializer):
    """Read projection of one plan item."""

    class Meta:
        model = PlanItem
        fields = [
            "id",
            "plan",
            "tooth_state",
            "procedure_code",
            "description",
            "tooth",
            "surfaces",
            "quantity",
            "unit_price_minor",
            "discount_minor",
            "tax_rate_bp",
            "line_total_minor",
            "status",
            "sequence",
            "notes",
        ]
        read_only_fields = fields


class PlanItemWriteSerializer(serializers.Serializer):
    """Input for one plan item; the line total is computed by the server."""

    procedure_code = serializers.CharField(max_length=64)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    tooth = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=99)
    surfaces = serializers.ListField(
        child=serializers.CharField(max_length=8), required=False, default=list
    )
    quantity = serializers.IntegerField(required=False, min_value=1, default=1)
    unit_price_minor = serializers.IntegerField(required=False, min_value=0, default=0)
    discount_minor = serializers.IntegerField(required=False, min_value=0, default=0)
    tax_rate_bp = serializers.IntegerField(required=False, min_value=0, default=0)
    status = serializers.ChoiceField(
        choices=PlanItem.Status.choices, required=False, default=PlanItem.Status.PROPOSED
    )
    tooth_state = serializers.UUIDField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_tooth_state(self, value):
        if value is None:
            return value
        from apps.charting.models import ToothState

        if not ToothState.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("tooth state not found in this clinic")
        return value


class TreatmentPlanSerializer(serializers.ModelSerializer):
    """Read projection of a plan including its items and totals."""

    items = PlanItemSerializer(many=True, read_only=True)

    class Meta:
        model = TreatmentPlan
        fields = [
            "id",
            "patient",
            "provider",
            "title",
            "status",
            "currency",
            "subtotal_minor",
            "discount_minor",
            "tax_minor",
            "total_minor",
            "notes",
            "expires_at",
            "presented_at",
            "accepted_at",
            "accepted_by",
            "version",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class TreatmentPlanCreateSerializer(serializers.Serializer):
    patient = serializers.UUIDField()
    title = serializers.CharField(max_length=255)
    currency = serializers.CharField(required=False, allow_blank=True, max_length=3)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    provider = serializers.UUIDField(required=False, allow_null=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    items = PlanItemWriteSerializer(many=True, required=False, default=list)

    def validate_patient(self, value):
        if not _patient_exists(_clinic(self.context), value):
            raise serializers.ValidationError("patient not found in this clinic")
        return value

    def validate_provider(self, value):
        if value is None:
            return value
        if not Membership.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("provider not found in this clinic")
        return value


class TreatmentPlanUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    notes = serializers.CharField(required=False, allow_blank=True)
    provider = serializers.UUIDField(required=False, allow_null=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    items = PlanItemWriteSerializer(many=True, required=False)

    def validate_provider(self, value):
        if value is None:
            return value
        if not Membership.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("provider not found in this clinic")
        return value


class ConsentTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentTemplate
        fields = [
            "id",
            "key",
            "name",
            "purpose",
            "body",
            "locale",
            "version",
            "is_active",
            "approval_state",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "version", "created_at", "updated_at"]

    def validate_key(self, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise serializers.ValidationError("This field may not be blank.")
        return cleaned

    def validate(self, attrs):
        clinic = _clinic(self.context)
        key = attrs.get("key", getattr(self.instance, "key", None))
        if key:
            existing = ConsentTemplate.all_objects.filter(clinic=clinic, key=key)
            if self.instance is not None:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise serializers.ValidationError({"key": ["already used in this clinic"]})
        return attrs

    def create(self, validated_data):
        clinic = _clinic(self.context)
        return ConsentTemplate.all_objects.create(clinic=clinic, version=1, **validated_data)


class ConsentRecordSerializer(serializers.ModelSerializer):
    legal_notice = serializers.SerializerMethodField()
    is_legally_compliant = serializers.SerializerMethodField()

    class Meta:
        model = ConsentRecord
        fields = [
            "id",
            "patient",
            "template",
            "template_version",
            "signer_name",
            "relationship",
            "signature_type",
            "signature_document",
            "signed_at",
            "document_hash",
            "status",
            "legal_notice",
            "is_legally_compliant",
            "created_at",
        ]
        read_only_fields = fields

    def get_legal_notice(self, obj: ConsentRecord) -> str:
        return signature_service.legal_notice(obj.signature_type)

    def get_is_legally_compliant(self, obj: ConsentRecord) -> bool:
        return obj.signature_type not in signature_service.LEGAL_NOTICE_TYPES


class PlanConsentCreateSerializer(serializers.Serializer):
    patient = serializers.UUIDField()
    template = serializers.UUIDField()
    signer_name = serializers.CharField(max_length=255)
    relationship = serializers.CharField(required=False, allow_blank=True, default="")
    signature_type = serializers.ChoiceField(
        choices=ConsentRecord.SignatureType.choices,
        required=False,
        default=ConsentRecord.SignatureType.TYPED,
    )
    signature_document = serializers.UUIDField(required=False, allow_null=True)

    def validate_patient(self, value):
        if not _patient_exists(_clinic(self.context), value):
            raise serializers.ValidationError("patient not found in this clinic")
        return value

    def validate_template(self, value):
        if not ConsentTemplate.all_objects.filter(
            pk=value, clinic=_clinic(self.context)
        ).exists():
            raise serializers.ValidationError("template not found in this clinic")
        return value

    def validate_signature_document(self, value):
        if value is None:
            return value
        if not Document.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("document not found in this clinic")
        return value


class DocumentSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            "id",
            "patient",
            "kind",
            "filename",
            "content_type",
            "size_bytes",
            "sha256",
            "storage_key",
            "plan",
            "consent",
            "download_url",
            "created_at",
        ]
        read_only_fields = fields

    def get_download_url(self, obj: Document) -> str:
        token = document_service.issue_download_token(obj)
        return f"{document_service.download_path(obj)}?token={token}"


__all__ = [
    "ConsentRecordSerializer",
    "ConsentTemplateSerializer",
    "DocumentSerializer",
    "PlanConsentCreateSerializer",
    "PlanItemSerializer",
    "PlanItemWriteSerializer",
    "TreatmentPlanCreateSerializer",
    "TreatmentPlanSerializer",
    "TreatmentPlanUpdateSerializer",
]
