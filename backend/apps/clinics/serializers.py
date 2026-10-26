"""Serializers for clinic, location, membership and invitation endpoints."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rest_framework import serializers

from apps.clinics.models import Clinic, Invitation, Location, Membership
from apps.clinics.permissions import ALL_PERMISSIONS


class ClinicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Clinic
        fields = [
            "id",
            "name",
            "slug",
            "timezone",
            "currency",
            "locale",
            "country",
            "phone",
            "address_json",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "status", "created_at", "updated_at"]


class ClinicWithRoleSerializer(ClinicSerializer):
    """Clinic payload plus the caller's membership summary (list endpoint)."""

    role = serializers.CharField(read_only=True)
    membership_id = serializers.UUIDField(read_only=True)

    class Meta(ClinicSerializer.Meta):
        fields = [*ClinicSerializer.Meta.fields, "role", "membership_id"]
        read_only_fields = fields


class ClinicCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    slug = serializers.SlugField(max_length=64)
    timezone = serializers.CharField(max_length=64, default="Asia/Kolkata")
    currency = serializers.RegexField(r"^[A-Za-z]{3}$", default="INR")
    locale = serializers.CharField(max_length=16, default="en-IN")
    country = serializers.CharField(max_length=2, default="IN")

    def validate_slug(self, value: str) -> str:
        normalized = value.strip().lower()
        if Clinic.objects.filter(slug=normalized).exists():
            raise serializers.ValidationError("A clinic with this slug already exists.")
        return normalized

    def validate_timezone(self, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise serializers.ValidationError("Unknown timezone.") from None
        return value


class ClinicUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    timezone = serializers.CharField(max_length=64, required=False)
    currency = serializers.RegexField(r"^[A-Za-z]{3}$", required=False)
    locale = serializers.CharField(max_length=16, required=False)
    country = serializers.CharField(max_length=2, required=False)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    address_json = serializers.JSONField(required=False)

    def validate_timezone(self, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise serializers.ValidationError("Unknown timezone.") from None
        return value


class UserSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    phone = serializers.CharField(read_only=True)


class MembershipSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id",
            "clinic_id",
            "user",
            "role",
            "status",
            "is_active",
            "permission_overrides",
            "accepted_at",
            "created_at",
        ]
        read_only_fields = fields


class MembershipWithClinicSerializer(MembershipSerializer):
    clinic = ClinicSerializer(read_only=True)

    class Meta(MembershipSerializer.Meta):
        fields = [*MembershipSerializer.Meta.fields, "clinic"]
        read_only_fields = fields


MEMBERSHIP_UPDATE_STATUS_CHOICES = [
    Membership.Status.ACTIVE,
    Membership.Status.SUSPENDED,
]


class MembershipUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Membership.Role.choices, required=False)
    status = serializers.ChoiceField(
        choices=MEMBERSHIP_UPDATE_STATUS_CHOICES,
        required=False,
    )
    permission_overrides = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )

    def validate_permission_overrides(self, value: list[str]) -> list[str]:
        unknown = sorted({item for item in value if item not in ALL_PERMISSIONS})
        if unknown:
            raise serializers.ValidationError(
                f"Unknown permission(s): {', '.join(unknown)}"
            )
        return sorted(set(value))


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = [
            "id",
            "name",
            "timezone",
            "address_json",
            "phone",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class LocationCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    timezone = serializers.CharField(max_length=64, required=False, allow_blank=True)
    address_json = serializers.JSONField(required=False)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False, default=True)


class InvitationCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=Membership.Role.choices)

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class InvitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invitation
        fields = [
            "id",
            "clinic_id",
            "email",
            "role",
            "expires_at",
            "accepted_at",
            "revoked_at",
            "created_at",
        ]
        read_only_fields = fields
