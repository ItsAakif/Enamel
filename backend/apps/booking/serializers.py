"""Public booking request serializers (T112).

Only input shapes live here: the response projections are built in
``resources.py`` so the payload of an unauthenticated endpoint is easy to audit
for accidental PHI. Every accepted field is declared explicitly, so no model
column can be mass-assigned from a public request.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.scheduling.serializers import AwareDateTimeField

from .models import BookingConsent


class PublicBookingPatientSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=128)
    last_name = serializers.CharField(max_length=128)
    date_of_birth = serializers.DateField(required=False, allow_null=True, default=None)
    phone = serializers.CharField(max_length=32)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_first_name(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("This field may not be blank.")
        return cleaned

    def validate_last_name(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("This field may not be blank.")
        return cleaned

    def validate_date_of_birth(self, value):
        if value is None:
            return None
        from django.utils import timezone

        if value >= timezone.localdate():
            raise serializers.ValidationError("must be in the past")
        return value


class PublicBookingCreateSerializer(serializers.Serializer):
    appointment_type = serializers.UUIDField()
    location = serializers.UUIDField(required=False, allow_null=True, default=None)
    slot_start = AwareDateTimeField()
    patient = PublicBookingPatientSerializer()
    consents = serializers.DictField(
        child=serializers.BooleanField(),
        required=False,
        default=dict,
    )
    idempotency_key = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=255
    )
    # Honeypot: real clients leave this blank.
    website = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_consents(self, value: dict) -> dict:
        valid = set(BookingConsent.Purpose.values)
        unknown = sorted(str(key) for key in value if str(key) not in valid)
        if unknown:
            raise serializers.ValidationError(
                f"unknown consent purpose(s): {', '.join(unknown)}"
            )
        return value


class PublicVerifySerializer(serializers.Serializer):
    code = serializers.CharField(required=False, allow_blank=True, default="")


class PublicLinkActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["cancel", "reschedule"])
    start_at = AwareDateTimeField(required=False, allow_null=True, default=None)


__all__ = [
    "PublicBookingCreateSerializer",
    "PublicBookingPatientSerializer",
    "PublicLinkActionSerializer",
    "PublicVerifySerializer",
]
