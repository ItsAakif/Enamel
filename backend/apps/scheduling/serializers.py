"""Scheduling serializers (T110).

Read serializers are ``ModelSerializer`` subclasses with an explicit field list,
so no model column can be mass-assigned. Write serializers declare the accepted
input themselves and validate every referenced object *inside the request
clinic*: a foreign id is a field error, never a cross-tenant read.
"""

from __future__ import annotations

from datetime import date, time

from rest_framework import serializers

from apps.clinics.models import Location, Membership, Provider
from apps.clinics.permissions import ROLES

from .models import (
    Appointment,
    AppointmentType,
    AvailabilityRule,
    Operatory,
    ScheduleException,
    WaitlistEntry,
    WaitlistOffer,
)
from .status import ALL_STATUSES


def _clinic(context) -> object:
    clinic = context.get("clinic")
    if clinic is None:
        raise serializers.ValidationError("A clinic scope is required.")
    return clinic


class AwareDateTimeField(serializers.DateTimeField):
    """Require an explicit UTC offset so an instant is never ambiguous.

    DRF otherwise interprets a naive string in the *server* default zone, which
    may differ from the clinic zone. Every appointment datetime is stored as a
    UTC instant, so a missing offset is a client error.
    """

    def to_internal_value(self, data):
        if isinstance(data, str):
            from django.utils import timezone as django_timezone
            from django.utils.dateparse import parse_datetime

            parsed = parse_datetime(data.strip())
            if parsed is None or django_timezone.is_naive(parsed):
                raise serializers.ValidationError(
                    "must include a timezone offset (for example +05:30 or Z)"
                )
        return super().to_internal_value(data)


class FkIdModelSerializer(serializers.ModelSerializer):
    """``ModelSerializer`` whose FK fields accept plain UUIDs on the wire.

    Django refuses ``instance.fk = <uuid>`` for a forward foreign key, so the
    UUID is mapped onto the ``*_id`` attname in ``create``/``update``.
    """

    #: wire field name -> model attname
    FK_ATTNAMES: dict[str, str] = {}

    def _split_fk_ids(self, validated_data):
        ids: dict = {}
        rest: dict = {}
        for name, value in validated_data.items():
            attname = self.FK_ATTNAMES.get(name)
            if attname is None:
                rest[name] = value
            else:
                ids[attname] = value
        return ids, rest

    def create(self, validated_data, **kwargs):
        # DRF merges ``save(**kwargs)`` into ``validated_data``; pull the clinic
        # back out before splitting FK ids.
        clinic = (
            validated_data.pop("clinic", None)
            or kwargs.get("clinic")
            or self.context.get("clinic")
        )
        ids, rest = self._split_fk_ids(validated_data)
        return self.Meta.model.all_objects.create(clinic=clinic, **ids, **rest)

    def update(self, instance, validated_data):
        validated_data.pop("clinic", None)
        ids, rest = self._split_fk_ids(validated_data)
        for name, value in {**ids, **rest}.items():
            setattr(instance, name, value)
        instance.save()
        return instance


# --------------------------------------------------------------------------- #
# Appointment types
# --------------------------------------------------------------------------- #


class AppointmentTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppointmentType
        fields = [
            "id",
            "code",
            "name",
            "duration_min",
            "buffer_before_min",
            "buffer_after_min",
            "requires_operatory",
            "public_bookable",
            "eligible_roles",
            "color",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_code(self, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise serializers.ValidationError("This field may not be blank.")
        return cleaned

    def validate_duration_min(self, value: int) -> int:
        if value < 1 or value > 24 * 60:
            raise serializers.ValidationError("must be between 1 and 1440 minutes")
        return value

    def validate_eligible_roles(self, value: list[str]) -> list[str]:
        if value is None:
            return []
        unknown = sorted({str(item) for item in value} - set(ROLES))
        if unknown:
            raise serializers.ValidationError(f"Unknown role(s): {', '.join(unknown)}")
        return sorted({str(item) for item in value})

    def validate(self, attrs):
        clinic = _clinic(self.context)
        code = attrs.get("code", getattr(self.instance, "code", None))
        if code:
            existing = AppointmentType.all_objects.filter(clinic=clinic, code=code)
            if self.instance is not None:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise serializers.ValidationError({"code": ["already used in this clinic"]})
        return attrs


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


class ProviderSerializer(FkIdModelSerializer):
    """A bookable clinician (T112 provider CRUD).

    ``membership`` is the link to the clinic member; the read-only ``user_name``
    and ``role`` come from that membership so the schedule can show who a
    provider is without a second lookup. No member email/phone is exposed here:
    the scheduling UI only needs a display name.
    """

    membership = serializers.UUIDField()
    user_name = serializers.SerializerMethodField()
    role = serializers.CharField(source="membership.role", read_only=True)

    FK_ATTNAMES = {"membership": "membership_id"}

    class Meta:
        model = Provider
        fields = [
            "id",
            "membership",
            "user_name",
            "role",
            "display_name",
            "specialty",
            "color",
            "is_bookable",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user_name", "role", "created_at", "updated_at"]

    def get_user_name(self, obj) -> str:
        membership = getattr(obj, "membership", None)
        if membership is None:
            return ""
        user = getattr(membership, "user", None)
        return getattr(user, "full_name", "") if user is not None else ""

    def validate_membership(self, value):
        clinic = _clinic(self.context)
        membership = Membership.all_objects.filter(pk=value, clinic=clinic).first()
        if membership is None:
            raise serializers.ValidationError("membership not found in this clinic")
        existing = Provider.all_objects.filter(clinic=clinic, membership_id=value)
        if self.instance is not None:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError("this member already has a provider profile")
        return value

    def validate_display_name(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("This field may not be blank.")
        return cleaned


# --------------------------------------------------------------------------- #
# Operatories
# --------------------------------------------------------------------------- #


class OperatorySerializer(FkIdModelSerializer):
    location = serializers.UUIDField()

    FK_ATTNAMES = {"location": "location_id"}

    class Meta:
        model = Operatory
        fields = [
            "id",
            "location",
            "name",
            "equipment_json",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_location(self, value):
        if not Location.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("location not found in this clinic")
        return value

    def validate_name(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("This field may not be blank.")
        return cleaned

    def validate(self, attrs):
        clinic = _clinic(self.context)
        location = attrs.get("location", getattr(self.instance, "location_id", None))
        name = attrs.get("name", getattr(self.instance, "name", None))
        if location and name:
            existing = Operatory.all_objects.filter(
                clinic=clinic, location_id=location, name=name
            )
            if self.instance is not None:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise serializers.ValidationError(
                    {"name": ["already used at this location"]}
                )
        return attrs


# --------------------------------------------------------------------------- #
# Availability rules
# --------------------------------------------------------------------------- #


class AvailabilityRuleSerializer(FkIdModelSerializer):
    provider = serializers.UUIDField(required=False, allow_null=True)
    location = serializers.UUIDField()

    FK_ATTNAMES = {"provider": "provider_id", "location": "location_id"}

    class Meta:
        model = AvailabilityRule
        fields = [
            "id",
            "provider",
            "location",
            "kind",
            "weekday",
            "start_time",
            "end_time",
            "valid_from",
            "valid_to",
            "timezone",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_provider(self, value):
        if value is None:
            return None
        if not Provider.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("provider not found in this clinic")
        return value

    def validate_location(self, value):
        if not Location.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("location not found in this clinic")
        return value

    def validate_weekday(self, value: int) -> int:
        if value < 0 or value > 6:
            raise serializers.ValidationError("must be between 0 (Monday) and 6 (Sunday)")
        return value

    def validate(self, attrs):
        start: time = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end: time = attrs.get("end_time", getattr(self.instance, "end_time", None))
        if start is not None and end is not None and start >= end:
            raise serializers.ValidationError({"end_time": ["must be after start_time"]})

        valid_from: date | None = attrs.get(
            "valid_from", getattr(self.instance, "valid_from", None)
        )
        valid_to: date | None = attrs.get("valid_to", getattr(self.instance, "valid_to", None))
        if valid_from and valid_to and valid_to < valid_from:
            raise serializers.ValidationError({"valid_to": ["must not be before valid_from"]})

        kind = attrs.get("kind", getattr(self.instance, "kind", AvailabilityRule.Kind.WORKING))
        if kind in (AvailabilityRule.Kind.BREAK, AvailabilityRule.Kind.BLOCK):
            self._validate_inside_working(attrs, start, end)
        return attrs

    def _validate_inside_working(self, attrs, start: time | None, end: time | None) -> None:
        clinic = _clinic(self.context)
        location = attrs.get("location", getattr(self.instance, "location_id", None))
        provider = attrs.get("provider", getattr(self.instance, "provider_id", None))
        weekday = attrs.get("weekday", getattr(self.instance, "weekday", None))
        if location is None or weekday is None or start is None or end is None:
            return

        working = AvailabilityRule.all_objects.filter(
            clinic=clinic,
            location_id=location,
            provider_id=provider,
            weekday=weekday,
            kind=AvailabilityRule.Kind.WORKING,
        )
        if self.instance is not None:
            working = working.exclude(pk=self.instance.pk)
        contained = any(
            rule.start_time <= start and rule.end_time >= end for rule in working
        )
        if not contained:
            raise serializers.ValidationError(
                {
                    "start_time": [
                        "a break or block must fall inside a working rule for the "
                        "same provider, location and weekday"
                    ]
                }
            )


# --------------------------------------------------------------------------- #
# Schedule exceptions
# --------------------------------------------------------------------------- #


class ScheduleExceptionSerializer(FkIdModelSerializer):
    provider = serializers.UUIDField(required=False, allow_null=True)
    location = serializers.UUIDField()

    FK_ATTNAMES = {"provider": "provider_id", "location": "location_id"}

    class Meta:
        model = ScheduleException
        fields = [
            "id",
            "provider",
            "location",
            "date",
            "start_time",
            "end_time",
            "all_day",
            "reason",
            "kind",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_provider(self, value):
        if value is None:
            return None
        if not Provider.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("provider not found in this clinic")
        return value

    def validate_location(self, value):
        if not Location.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("location not found in this clinic")
        return value

    def validate(self, attrs):
        all_day = attrs.get("all_day", getattr(self.instance, "all_day", True))
        start = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end = attrs.get("end_time", getattr(self.instance, "end_time", None))
        if all_day:
            if start is not None or end is not None:
                raise serializers.ValidationError(
                    {"start_time": ["an all-day exception may not set start_time or end_time"]}
                )
        else:
            if start is None or end is None:
                raise serializers.ValidationError(
                    {"start_time": ["a partial-day exception needs start_time and end_time"]}
                )
            if start >= end:
                raise serializers.ValidationError({"end_time": ["must be after start_time"]})
        return attrs


# --------------------------------------------------------------------------- #
# Appointments
# --------------------------------------------------------------------------- #


class AppointmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Appointment
        fields = [
            "id",
            "clinic_id",
            "location",
            "patient",
            "provider",
            "operatory",
            "appointment_type",
            "start_at",
            "end_at",
            "status",
            "source",
            "reason",
            "notes",
            "cancellation_reason",
            "rescheduled_from",
            "hold_expires_at",
            "version",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class AppointmentWriteSerializer(serializers.Serializer):
    """Create payload. ``end_at`` defaults to ``start_at + type.duration_min``."""

    patient = serializers.UUIDField()
    provider = serializers.UUIDField()
    appointment_type = serializers.UUIDField()
    location = serializers.UUIDField()
    operatory = serializers.UUIDField(required=False, allow_null=True, default=None)
    start_at = AwareDateTimeField()
    end_at = AwareDateTimeField(required=False, allow_null=True, default=None)
    source = serializers.ChoiceField(
        choices=Appointment.Source.choices,
        required=False,
        default=Appointment.Source.STAFF,
    )
    status = serializers.ChoiceField(choices=ALL_STATUSES, required=False, default=None)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    idempotency_key = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=255
    )

    def validate_patient(self, value):
        from apps.patients.models import Patient

        if not Patient.all_objects.filter(
            pk=value, clinic=_clinic(self.context), deleted_at__isnull=True
        ).exists():
            raise serializers.ValidationError("patient not found in this clinic")
        return value

    def validate_provider(self, value):
        if not Provider.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("provider not found in this clinic")
        return value

    def validate_appointment_type(self, value):
        if not AppointmentType.all_objects.filter(
            pk=value, clinic=_clinic(self.context)
        ).exists():
            raise serializers.ValidationError("appointment type not found in this clinic")
        return value

    def validate_location(self, value):
        if not Location.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("location not found in this clinic")
        return value

    def validate_operatory(self, value):
        if value is None:
            return None
        if not Operatory.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("operatory not found in this clinic")
        return value


class AppointmentPatchSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    operatory = serializers.UUIDField(required=False, allow_null=True)
    appointment_type = serializers.UUIDField(required=False)
    version = serializers.IntegerField(required=False, min_value=1)

    def validate_operatory(self, value):
        if value is None:
            return None
        if not Operatory.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("operatory not found in this clinic")
        return value

    def validate_appointment_type(self, value):
        if not AppointmentType.all_objects.filter(
            pk=value, clinic=_clinic(self.context)
        ).exists():
            raise serializers.ValidationError("appointment type not found in this clinic")
        return value

    def to_model_fields(self) -> dict:
        mapping = {
            "operatory": "operatory_id",
            "appointment_type": "appointment_type_id",
        }
        return {
            mapping.get(name, name): value
            for name, value in self.validated_data.items()
            if name != "version"
        }


class AppointmentCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)


class AppointmentRescheduleSerializer(serializers.Serializer):
    start_at = AwareDateTimeField()
    operatory = serializers.UUIDField(required=False, allow_null=True, default=None)
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_operatory(self, value):
        if value is None:
            return None
        if not Operatory.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("operatory not found in this clinic")
        return value


# --------------------------------------------------------------------------- #
# Waitlist
# --------------------------------------------------------------------------- #


class WaitlistEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = WaitlistEntry
        fields = [
            "id",
            "patient",
            "appointment_type",
            "provider_pref",
            "window_start",
            "window_end",
            "urgency",
            "status",
            "consent_snapshot",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class WaitlistEntryWriteSerializer(serializers.Serializer):
    patient = serializers.UUIDField()
    appointment_type = serializers.UUIDField()
    provider_pref = serializers.UUIDField(required=False, allow_null=True, default=None)
    window_start = AwareDateTimeField()
    window_end = AwareDateTimeField()
    urgency = serializers.IntegerField(required=False, min_value=1, max_value=5, default=3)
    consent_snapshot = serializers.DictField(required=False, default=dict)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_patient(self, value):
        from apps.patients.models import Patient

        if not Patient.all_objects.filter(
            pk=value, clinic=_clinic(self.context), deleted_at__isnull=True
        ).exists():
            raise serializers.ValidationError("patient not found in this clinic")
        return value

    def validate_appointment_type(self, value):
        if not AppointmentType.all_objects.filter(
            pk=value, clinic=_clinic(self.context)
        ).exists():
            raise serializers.ValidationError("appointment type not found in this clinic")
        return value

    def validate_provider_pref(self, value):
        if value is None:
            return None
        if not Provider.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("provider not found in this clinic")
        return value

    def validate(self, attrs):
        if attrs["window_end"] <= attrs["window_start"]:
            raise serializers.ValidationError(
                {"window_end": ["must be after window_start"]}
            )
        return attrs


class WaitlistEntryPatchSerializer(serializers.Serializer):
    window_start = AwareDateTimeField(required=False)
    window_end = AwareDateTimeField(required=False)
    urgency = serializers.IntegerField(required=False, min_value=1, max_value=5)
    provider_pref = serializers.UUIDField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    status = serializers.ChoiceField(
        choices=[
            (WaitlistEntry.Status.ACTIVE, "Active"),
            (WaitlistEntry.Status.CANCELLED, "Cancelled"),
        ],
        required=False,
    )

    def validate_provider_pref(self, value):
        if value is None:
            return None
        if not Provider.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("provider not found in this clinic")
        return value

    def to_model_fields(self) -> dict:
        mapping = {"provider_pref": "provider_pref_id"}
        return {
            mapping.get(name, name): value for name, value in self.validated_data.items()
        }


class WaitlistOfferCreateSerializer(serializers.Serializer):
    start_at = AwareDateTimeField()
    provider = serializers.UUIDField(required=False, allow_null=True, default=None)
    operatory = serializers.UUIDField(required=False, allow_null=True, default=None)
    ttl_minutes = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=15, max_value=240
    )

    def validate_provider(self, value):
        if value is None:
            return None
        if not Provider.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("provider not found in this clinic")
        return value

    def validate_operatory(self, value):
        if value is None:
            return None
        if not Operatory.all_objects.filter(pk=value, clinic=_clinic(self.context)).exists():
            raise serializers.ValidationError("operatory not found in this clinic")
        return value


class WaitlistOfferSerializer(serializers.ModelSerializer):
    token = serializers.SerializerMethodField()

    class Meta:
        model = WaitlistOffer
        fields = [
            "id",
            "entry",
            "start_at",
            "end_at",
            "provider",
            "operatory",
            "token",
            "score_json",
            "expires_at",
            "status",
            "claimed_appointment",
            "created_at",
        ]
        read_only_fields = fields

    def get_token(self, obj) -> str | None:
        return self.context.get("token")


class RecommendationSerializer(serializers.Serializer):
    entry_id = serializers.UUIDField()
    patient_id = serializers.UUIDField()
    provider_id = serializers.UUIDField(allow_null=True)
    operatory_id = serializers.UUIDField(allow_null=True)
    start_at = serializers.DateTimeField()
    end_at = serializers.DateTimeField()
    gap_reason = serializers.CharField()
    total = serializers.FloatField()
    components = serializers.ListField(child=serializers.DictField())
    reasons = serializers.ListField(child=serializers.CharField())


class GapSerializer(serializers.Serializer):
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    provider_id = serializers.CharField(allow_null=True)
    operatory_id = serializers.CharField(allow_null=True)
    location_id = serializers.CharField(allow_null=True)
    appointment_type_id = serializers.CharField(allow_null=True)
    reason = serializers.CharField()


__all__ = [
    "AppointmentCancelSerializer",
    "AppointmentPatchSerializer",
    "AppointmentRescheduleSerializer",
    "AppointmentSerializer",
    "AppointmentTypeSerializer",
    "AppointmentWriteSerializer",
    "AvailabilityRuleSerializer",
    "AwareDateTimeField",
    "FkIdModelSerializer",
    "GapSerializer",
    "OperatorySerializer",
    "ProviderSerializer",
    "RecommendationSerializer",
    "ScheduleExceptionSerializer",
    "WaitlistEntryPatchSerializer",
    "WaitlistEntrySerializer",
    "WaitlistEntryWriteSerializer",
    "WaitlistOfferCreateSerializer",
    "WaitlistOfferSerializer",
]
