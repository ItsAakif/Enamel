"""Marketing serializers (T132).

Write serializers validate every referenced object inside the request clinic and
reject a clinical segment predicate at the field level, so an unsupported field
is a ``predicate_json`` field error rather than a 500.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.messaging.models import MessageTemplate

from .models import Campaign, CampaignRecipient, Segment
from .services import segments
from .services.guardrails import PredicateError


def _clinic(context) -> object:
    clinic = context.get("clinic")
    if clinic is None:
        raise serializers.ValidationError("A clinic scope is required.")
    return clinic


def _user(context):
    return context.get("user")


def validate_predicate_json(value):
    """Field validator shared by the segment serializers."""

    try:
        return segments.validate_predicate(value)
    except PredicateError as exc:
        raise serializers.ValidationError(exc.message) from exc


class SegmentSerializer(serializers.ModelSerializer):
    predicate_json = serializers.JSONField()

    class Meta:
        model = Segment
        fields = [
            "id",
            "name",
            "predicate_json",
            "is_active",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]

    def validate_predicate_json(self, value):
        return validate_predicate_json(value)

    def validate(self, attrs):
        clinic = _clinic(self.context)
        name = attrs.get("name", getattr(self.instance, "name", None))
        if name:
            existing = Segment.all_objects.filter(clinic=clinic, name=name)
            if self.instance is not None:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise serializers.ValidationError({"name": ["already used in this clinic"]})
        return attrs

    def create(self, validated_data):
        clinic = _clinic(self.context)
        return Segment.all_objects.create(
            clinic=clinic, created_by=_user(self.context), **validated_data
        )


class SegmentPreviewRequestSerializer(serializers.Serializer):
    predicate_json = serializers.JSONField()

    def validate_predicate_json(self, value):
        return validate_predicate_json(value)


class CampaignSerializer(serializers.ModelSerializer):
    class Meta:
        model = Campaign
        fields = [
            "id",
            "name",
            "segment",
            "template",
            "channel",
            "status",
            "created_by",
            "approved_by",
            "approved_at",
            "scheduled_at",
            "daily_cap",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "created_by",
            "approved_by",
            "approved_at",
            "created_at",
            "updated_at",
        ]

    def validate_segment(self, value):
        clinic = _clinic(self.context)
        if not Segment.all_objects.filter(clinic=clinic, pk=value.pk).exists():
            raise serializers.ValidationError("segment not found in this clinic")
        return value

    def validate_template(self, value):
        if value is None:
            return value
        clinic = _clinic(self.context)
        if not MessageTemplate.all_objects.filter(clinic=clinic, pk=value.pk).exists():
            raise serializers.ValidationError("template not found in this clinic")
        return value

    def create(self, validated_data):
        clinic = _clinic(self.context)
        return Campaign.all_objects.create(
            clinic=clinic,
            created_by=_user(self.context),
            status=Campaign.Status.DRAFT,
            **validated_data,
        )


class CampaignRecipientSerializer(serializers.ModelSerializer):
    class Meta:
        model = CampaignRecipient
        fields = [
            "id",
            "campaign",
            "patient",
            "status",
            "skip_reason",
            "message",
            "created_at",
        ]
        read_only_fields = fields


class CampaignMetricsSerializer(serializers.Serializer):
    campaign_id = serializers.UUIDField()
    status = serializers.CharField()
    recipients = serializers.DictField()
    messages = serializers.DictField()
    provider_events = serializers.DictField()
    engagement = serializers.DictField()


__all__ = [
    "CampaignMetricsSerializer",
    "CampaignRecipientSerializer",
    "CampaignSerializer",
    "SegmentPreviewRequestSerializer",
    "SegmentSerializer",
    "validate_predicate_json",
]
