"""Messaging serializers (T114).

Read serializers expose only non-PHI fields: consent evidence payloads and IP
hashes are never returned, and message destinations are shown as hashes, never
raw addresses. Write serializers validate every referenced object *inside the
request clinic*, so a foreign id is a field error rather than a cross-tenant
read.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.patients.models import Patient

from .models import (
    Consent,
    Message,
    MessageTemplate,
    RecallType,
    Suppression,
)


def _clinic(context) -> object:
    clinic = context.get("clinic")
    if clinic is None:
        raise serializers.ValidationError("A clinic scope is required.")
    return clinic


class ConsentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Consent
        fields = [
            "id",
            "patient",
            "channel",
            "purpose",
            "status",
            "source",
            "wording_version",
            "granted_at",
            "revoked_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ConsentCreateSerializer(serializers.Serializer):
    patient = serializers.UUIDField()
    channel = serializers.ChoiceField(choices=Consent.Channel.choices)
    purpose = serializers.ChoiceField(choices=Consent.Purpose.choices)
    source = serializers.CharField(required=False, allow_blank=True, default="", max_length=64)
    wording_version = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=32
    )
    evidence = serializers.DictField(required=False, default=dict)

    def validate_patient(self, value):
        if not Patient.all_objects.filter(
            pk=value, clinic=_clinic(self.context), deleted_at__isnull=True
        ).exists():
            raise serializers.ValidationError("patient not found in this clinic")
        return value


class SuppressionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Suppression
        fields = ["id", "channel", "address_hash", "reason", "created_at"]
        read_only_fields = fields


class MessageTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageTemplate
        fields = [
            "id",
            "key",
            "name",
            "channel",
            "purpose",
            "locale",
            "version",
            "subject",
            "body",
            "variables",
            "approval_status",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "version", "created_at", "updated_at"]

    def validate_key(self, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise serializers.ValidationError("This field may not be blank.")
        return cleaned

    def validate_variables(self, value):
        if value is None:
            return []
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise serializers.ValidationError("must be a list of variable names")
        return sorted({item.strip() for item in value if item.strip()})

    def validate(self, attrs):
        clinic = _clinic(self.context)
        key = attrs.get("key", getattr(self.instance, "key", None))
        if key:
            existing = MessageTemplate.all_objects.filter(clinic=clinic, key=key)
            if self.instance is not None:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise serializers.ValidationError({"key": ["already used in this clinic"]})
        return attrs

    def create(self, validated_data):
        clinic = _clinic(self.context)
        return MessageTemplate.all_objects.create(clinic=clinic, version=1, **validated_data)


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = [
            "id",
            "patient",
            "channel",
            "purpose",
            "template",
            "template_version",
            "to_address_hash",
            "status",
            "reason",
            "provider",
            "provider_message_id",
            "error_code",
            "cost_minor",
            "scheduled_for",
            "sent_at",
            "delivered_at",
            "attempts",
            "created_at",
        ]
        read_only_fields = fields


class RecallTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecallType
        fields = [
            "id",
            "name",
            "interval_months",
            "channel",
            "template_key",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_interval_months(self, value: int) -> int:
        if value < 1 or value > 120:
            raise serializers.ValidationError("must be between 1 and 120 months")
        return value

    def validate(self, attrs):
        clinic = _clinic(self.context)
        name = attrs.get("name", getattr(self.instance, "name", None))
        if name:
            existing = RecallType.all_objects.filter(clinic=clinic, name=name)
            if self.instance is not None:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise serializers.ValidationError({"name": ["already used in this clinic"]})
        return attrs

    def create(self, validated_data):
        clinic = _clinic(self.context)
        return RecallType.all_objects.create(clinic=clinic, **validated_data)


class RecallDueSerializer(serializers.Serializer):
    """Read-only projection of one due recall (see ``services.recall``)."""

    id = serializers.UUIDField()
    patient_id = serializers.UUIDField()
    recall_type_id = serializers.UUIDField()
    recall_type_name = serializers.CharField()
    channel = serializers.CharField()
    template_key = serializers.CharField()
    interval_months = serializers.IntegerField()
    due_at = serializers.DateTimeField()
    last_completed_at = serializers.DateTimeField(allow_null=True)
    status = serializers.CharField()


class RecallScheduleSerializer(serializers.Serializer):
    recall_type = serializers.UUIDField()

    def validate_recall_type(self, value):
        if not RecallType.all_objects.filter(
            pk=value, clinic=_clinic(self.context)
        ).exists():
            raise serializers.ValidationError("recall type not found in this clinic")
        return value


__all__ = [
    "ConsentCreateSerializer",
    "ConsentSerializer",
    "MessageSerializer",
    "MessageTemplateSerializer",
    "RecallScheduleSerializer",
    "RecallTypeSerializer",
    "SuppressionSerializer",
]
