"""Serializers for the patient domain (T106).

Write serializers are explicit ``Serializer`` subclasses rather than
``ModelSerializer`` so the accepted input is exactly the documented field list
(no mass assignment through a model field) and so phone numbers can be
normalized to E.164 and validated against the clinic.

One class serves create and partial update: DRF drops fields that are not in the
payload when ``partial=True`` (including their defaults), so a PATCH never
resets an unrelated column.
"""

from __future__ import annotations

from datetime import date

from django.apps import apps as django_apps
from django.utils import timezone
from rest_framework import serializers

from apps.clinics.models import Location, Membership
from apps.clinics.serializers import UserSummarySerializer

from .models import Allergy, Encounter, MedicalHistory, Patient
from .services.dedup import InvalidPhoneNumber, normalize_phone


def chart_summary(patient: Patient) -> dict:
    """Return odontogram counts without importing the charting app.

    Charting (T108) owns ``ToothState``. Until it is installed the patient
    detail payload carries ``{"available": false}``; when it exists the summary
    is a count per status, never the tooth data itself.
    """

    if not django_apps.is_installed("apps.charting"):
        return {"available": False}
    try:
        ToothState = django_apps.get_model("charting", "ToothState")
    except LookupError:  # pragma: no cover - app installed without the model
        return {"available": False}

    manager = getattr(ToothState, "all_objects", None)
    if manager is None:  # pragma: no cover - the real model exposes all_objects
        manager = ToothState._default_manager
    rows = (
        manager.filter(clinic_id=patient.clinic_id, patient_id=patient.pk)
        .values_list("status", flat=True)
    )
    counts: dict[str, int] = {}
    total = 0
    for status in rows:
        counts[str(status)] = counts.get(str(status), 0) + 1
        total += 1
    return {"available": True, "total": total, "by_status": counts}


class LocationSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ["id", "name", "is_active"]
        read_only_fields = fields


class ProviderSummarySerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "role", "user"]
        read_only_fields = fields


class PatientListSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    primary_location = LocationSummarySerializer(read_only=True)
    primary_provider = ProviderSummarySerializer(read_only=True)

    class Meta:
        model = Patient
        fields = [
            "id",
            "number",
            "first_name",
            "last_name",
            "full_name",
            "preferred_name",
            "date_of_birth",
            "sex_at_birth",
            "phone_e164",
            "email",
            "status",
            "source",
            "primary_location",
            "primary_provider",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class PatientDetailSerializer(PatientListSerializer):
    class Meta(PatientListSerializer.Meta):
        fields = [
            *PatientListSerializer.Meta.fields,
            "gender_identity",
            "address_json",
            "language",
            "preferred_contact",
            "merged_into",
            "deleted_at",
            "notes",
        ]
        read_only_fields = fields


class PatientWriteSerializer(serializers.Serializer):
    """Create/partial-update payload for a patient."""

    first_name = serializers.CharField(max_length=128)
    last_name = serializers.CharField(max_length=128)
    preferred_name = serializers.CharField(
        max_length=128, required=False, allow_blank=True, default=""
    )
    date_of_birth = serializers.DateField(required=False, allow_null=True, default=None)
    sex_at_birth = serializers.ChoiceField(
        choices=Patient.SexAtBirth.choices,
        required=False,
        default=Patient.SexAtBirth.UNKNOWN,
    )
    gender_identity = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default=""
    )
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    address_json = serializers.JSONField(required=False, default=dict)
    language = serializers.CharField(max_length=16, required=False, default="en")
    preferred_contact = serializers.ChoiceField(
        choices=Patient.PreferredContact.choices,
        required=False,
        default=Patient.PreferredContact.PHONE,
    )
    status = serializers.ChoiceField(
        choices=Patient.Status.choices,
        required=False,
        default=Patient.Status.ACTIVE,
    )
    primary_location = serializers.UUIDField(required=False, allow_null=True, default=None)
    primary_provider = serializers.UUIDField(required=False, allow_null=True, default=None)
    source = serializers.ChoiceField(
        choices=Patient.Source.choices,
        required=False,
        default=Patient.Source.STAFF,
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    # Accepted only on create; the view pops it before building model fields.
    override_duplicates = serializers.BooleanField(required=False, default=False, write_only=True)

    def _clinic(self):
        clinic = self.context.get("clinic")
        if clinic is None:
            raise serializers.ValidationError("A clinic scope is required.")
        return clinic

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

    def validate_date_of_birth(self, value: date | None) -> date | None:
        if value is None:
            return None
        today = timezone.localdate()
        if value >= today:
            raise serializers.ValidationError("must be in the past")
        if value.year < 1900:
            raise serializers.ValidationError("must not be earlier than 1900")
        return value

    def validate_phone(self, value: str) -> str:
        try:
            return normalize_phone(value)
        except InvalidPhoneNumber as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_email(self, value: str) -> str:
        return value.strip().lower()

    def validate_address_json(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("must be an object")
        return value

    def validate_primary_location(self, value):
        if value is None:
            return None
        clinic = self._clinic()
        if not Location.all_objects.filter(pk=value, clinic=clinic).exists():
            raise serializers.ValidationError("location not found in this clinic")
        return value

    def validate_primary_provider(self, value):
        if value is None:
            return None
        clinic = self._clinic()
        if not Membership.all_objects.filter(pk=value, clinic=clinic).exists():
            raise serializers.ValidationError("provider not found in this clinic")
        return value

    def to_model_fields(self) -> dict:
        """Return validated data keyed by model attribute name."""

        mapping = {
            "phone": "phone_e164",
            "primary_location": "primary_location_id",
            "primary_provider": "primary_provider_id",
        }
        fields = {}
        for name, value in self.validated_data.items():
            if name == "override_duplicates":
                continue
            fields[mapping.get(name, name)] = value
        return fields


class AllergySerializer(serializers.ModelSerializer):
    class Meta:
        model = Allergy
        fields = [
            "id",
            "patient_id",
            "substance",
            "reaction",
            "severity",
            "is_active",
            "recorded_by",
            "recorded_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "patient_id",
            "recorded_by",
            "recorded_at",
            "created_at",
            "updated_at",
        ]


class AllergyWriteSerializer(serializers.Serializer):
    substance = serializers.CharField(max_length=255)
    reaction = serializers.CharField(required=False, allow_blank=True, default="")
    severity = serializers.ChoiceField(
        choices=Allergy.Severity.choices,
        required=False,
        default=Allergy.Severity.MODERATE,
    )
    is_active = serializers.BooleanField(required=False, default=True)

    def validate_substance(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("This field may not be blank.")
        return cleaned


class MedicalHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = MedicalHistory
        fields = [
            "id",
            "patient_id",
            "kind",
            "label",
            "since",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "patient_id", "created_at", "updated_at"]


class MedicalHistoryWriteSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=MedicalHistory.Kind.choices)
    label = serializers.CharField(max_length=255)
    since = serializers.DateField(required=False, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    is_active = serializers.BooleanField(required=False, default=True)

    def validate_label(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("This field may not be blank.")
        return cleaned


class EncounterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Encounter
        fields = [
            "id",
            "patient_id",
            "provider",
            "appointment_id",
            "kind",
            "occurred_at",
            "chief_complaint",
            "notes",
            "diagnosis_codes",
            "status",
            "version",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "patient_id",
            "version",
            "created_at",
            "updated_at",
        ]


class EncounterWriteSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(
        choices=Encounter.Kind.choices,
        required=False,
        default=Encounter.Kind.EXAM,
    )
    occurred_at = serializers.DateTimeField(required=False, default=timezone.now)
    chief_complaint = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    diagnosis_codes = serializers.ListField(
        child=serializers.CharField(max_length=16),
        required=False,
        default=list,
    )
    status = serializers.ChoiceField(
        choices=Encounter.Status.choices,
        required=False,
        default=Encounter.Status.DRAFT,
    )
    appointment_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    provider = serializers.UUIDField(required=False, allow_null=True, default=None)

    def validate_diagnosis_codes(self, value: list[str]) -> list[str]:
        if len(value) > 50:
            raise serializers.ValidationError("at most 50 codes are allowed")
        return [code.strip() for code in value if code.strip()]

    def validate_provider(self, value):
        if value is None:
            return None
        clinic = self.context.get("clinic")
        if clinic is None:
            raise serializers.ValidationError("A clinic scope is required.")
        if not Membership.all_objects.filter(pk=value, clinic=clinic).exists():
            raise serializers.ValidationError("provider not found in this clinic")
        return value

    def to_model_fields(self) -> dict:
        """Return validated data keyed by model attribute name.

        ``provider`` is a UUID on the wire; the model expects the FK attname so
        a UUID can be assigned without loading the membership row.
        """

        mapping = {"provider": "provider_id"}
        return {
            mapping.get(name, name): value
            for name, value in self.validated_data.items()
        }


__all__ = [
    "AllergySerializer",
    "AllergyWriteSerializer",
    "EncounterSerializer",
    "EncounterWriteSerializer",
    "LocationSummarySerializer",
    "MedicalHistorySerializer",
    "MedicalHistoryWriteSerializer",
    "PatientDetailSerializer",
    "PatientListSerializer",
    "PatientWriteSerializer",
    "ProviderSummarySerializer",
    "chart_summary",
]
