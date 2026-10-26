"""Patient domain models (T106).

Every table carries the ``clinic`` discriminator from
:class:`apps.core.tenancy.ClinicScopedModel`, so the tenant manager and the
save guard apply to all of them. ``Allergy``, ``MedicalHistory`` and
``Encounter`` keep their own ``clinic`` column (rather than only reaching it
through the patient) because the tenant manager filters on ``clinic_id``
directly -- a patient-only FK would force a join on every scoped query.

``Patient.number`` is allocated per clinic by ``services.numbering`` and is
unique inside a clinic only (two clinics both have an ``EN-000001``).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.tenancy import ClinicScopedModel


class Patient(ClinicScopedModel):
    class SexAtBirth(models.TextChoices):
        FEMALE = "female", "Female"
        MALE = "male", "Male"
        INTERSEX = "intersex", "Intersex"
        UNKNOWN = "unknown", "Unknown"

    class PreferredContact(models.TextChoices):
        PHONE = "phone", "Phone"
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"
        WHATSAPP = "whatsapp", "WhatsApp"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        ARCHIVED = "archived", "Archived"
        DECEASED = "deceased", "Deceased"

    class Source(models.TextChoices):
        STAFF = "staff", "Staff"
        ONLINE = "online", "Online"
        AI = "ai", "AI assisted"
        IMPORT = "import", "Import"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    number = models.CharField(max_length=16)
    first_name = models.CharField(max_length=128)
    last_name = models.CharField(max_length=128)
    preferred_name = models.CharField(max_length=128, blank=True, default="")
    date_of_birth = models.DateField(null=True, blank=True)
    sex_at_birth = models.CharField(
        max_length=16,
        choices=SexAtBirth.choices,
        default=SexAtBirth.UNKNOWN,
    )
    gender_identity = models.CharField(max_length=64, blank=True, default="")
    # Stored in E.164 ("+919876543210"); normalization happens in the serializer.
    phone_e164 = models.CharField(max_length=20, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    address_json = models.JSONField(default=dict, blank=True)
    language = models.CharField(max_length=16, default="en")
    preferred_contact = models.CharField(
        max_length=16,
        choices=PreferredContact.choices,
        default=PreferredContact.PHONE,
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    primary_location = models.ForeignKey(
        "clinics.Location",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # Interim link, deliberately kept pointing at ``clinics.Membership`` even
    # though T110 introduced ``clinics.Provider``: scheduling owns the
    # ``Provider`` FK on appointments/rules, while this patient-level preference
    # migrates in a later data-carrying change (it would need a backfill from
    # membership to provider). Documented in ARCHITECTURE.md section 8.
    primary_provider = models.ForeignKey(
        "clinics.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.STAFF)
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merged_patients",
    )
    # Soft delete / archive marker. Rows with a value here are hidden from every
    # read path except an explicit "include deleted" lookup in the mutation views.
    deleted_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "patients_patient"
        ordering = ["last_name", "first_name", "id"]
        indexes = [
            models.Index(
                fields=["clinic", "last_name", "first_name"],
                name="pat_clinic_name_idx",
            ),
            models.Index(fields=["clinic", "phone_e164"], name="pat_clinic_phone_idx"),
            models.Index(fields=["clinic", "email"], name="pat_clinic_email_idx"),
            models.Index(fields=["clinic", "number"], name="pat_clinic_number_idx"),
            models.Index(fields=["clinic", "status"], name="pat_clinic_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "number"],
                name="uniq_patient_clinic_number",
            ),
        ]
        verbose_name = "Patient"
        verbose_name_plural = "Patients"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_archived(self) -> bool:
        """True for a soft-deleted or explicitly archived record."""

        return self.deleted_at is not None or self.status == self.Status.ARCHIVED

    def __str__(self) -> str:
        return f"{self.full_name} ({self.number})"


class PatientCounter(models.Model):
    """Per-clinic sequence row for ``Patient.number``.

    The row is locked with ``SELECT ... FOR UPDATE`` while a number is handed
    out, so concurrent creates in one clinic serialise and never skip or repeat
    a number. ``next_number`` is the *next* value to hand out (1 for a fresh
    clinic, producing ``EN-000001``).
    """

    clinic = models.OneToOneField(
        "clinics.Clinic",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="patient_counter",
    )
    next_number = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "patients_patientcounter"
        verbose_name = "Patient counter"
        verbose_name_plural = "Patient counters"

    def __str__(self) -> str:
        return f"{self.clinic_id}: {self.next_number}"


class Allergy(ClinicScopedModel):
    class Severity(models.TextChoices):
        MILD = "mild", "Mild"
        MODERATE = "moderate", "Moderate"
        SEVERE = "severe", "Severe"
        ANAPHYLAXIS = "anaphylaxis", "Anaphylaxis"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="allergies",
    )
    substance = models.CharField(max_length=255)
    reaction = models.TextField(blank=True, default="")
    severity = models.CharField(
        max_length=16,
        choices=Severity.choices,
        default=Severity.MODERATE,
    )
    is_active = models.BooleanField(default=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "patients_allergy"
        ordering = ["-recorded_at", "id"]
        indexes = [
            models.Index(fields=["clinic", "patient"], name="allergy_clinic_patient_idx"),
            models.Index(fields=["patient", "is_active"], name="allergy_patient_active_idx"),
        ]
        verbose_name = "Allergy"
        verbose_name_plural = "Allergies"

    def __str__(self) -> str:
        return f"{self.substance} ({self.severity})"


class MedicalHistory(ClinicScopedModel):
    class Kind(models.TextChoices):
        CONDITION = "condition", "Condition"
        SURGERY = "surgery", "Surgery"
        MEDICATION = "medication", "Medication"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="medical_history",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    label = models.CharField(max_length=255)
    since = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "patients_medicalhistory"
        ordering = ["kind", "label", "id"]
        indexes = [
            models.Index(fields=["clinic", "patient"], name="mh_clinic_patient_idx"),
            models.Index(fields=["patient", "is_active"], name="mh_patient_active_idx"),
        ]
        verbose_name = "Medical history entry"
        verbose_name_plural = "Medical history"

    def __str__(self) -> str:
        return f"{self.kind}: {self.label}"


class Encounter(ClinicScopedModel):
    class Kind(models.TextChoices):
        EXAM = "exam", "Exam"
        TREATMENT = "treatment", "Treatment"
        FOLLOW_UP = "follow_up", "Follow-up"
        EMERGENCY = "emergency", "Emergency"
        TELEHEALTH = "telehealth", "Telehealth"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        FINALIZED = "finalized", "Finalized"
        AMENDED = "amended", "Amended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="encounters",
    )
    provider = models.ForeignKey(
        "clinics.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # Plain UUID: scheduling (T110) owns the ``Appointment`` model, but the
    # encounter link stays an id so patients does not depend on scheduling.
    # ``services.encounters`` validates it through the app registry. Kept as
    # ``appointment_id`` so a future FK needs no data migration.
    appointment_id = models.UUIDField(null=True, blank=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.EXAM)
    occurred_at = models.DateTimeField(default=timezone.now)
    chief_complaint = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    diagnosis_codes = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "patients_encounter"
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["clinic", "patient"], name="enc_clinic_patient_idx"),
            models.Index(fields=["patient", "-occurred_at"], name="enc_patient_occurred_idx"),
        ]
        verbose_name = "Encounter"
        verbose_name_plural = "Encounters"

    def __str__(self) -> str:
        return f"{self.kind} @ {self.occurred_at:%Y-%m-%d}"


class PatientMergeLog(ClinicScopedModel):
    """Minimal record of a patient merge; the merge workflow UI is out of scope.

    The row keeps only references and a redacted snapshot, so merge decisions
    stay auditable without copying clinical free text.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    source_patient = models.ForeignKey(
        Patient,
        on_delete=models.PROTECT,
        related_name="merge_sources",
    )
    target_patient = models.ForeignKey(
        Patient,
        on_delete=models.PROTECT,
        related_name="merge_targets",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    snapshot_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "patients_patientmergelog"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["clinic", "created_at"], name="merge_clinic_created_idx"),
        ]
        verbose_name = "Patient merge log"
        verbose_name_plural = "Patient merge log"


# Stable OpenAPI enum names (SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]).
PATIENT_STATUS_CHOICES = Patient.Status.choices
PATIENT_SOURCE_CHOICES = Patient.Source.choices
PATIENT_SEX_AT_BIRTH_CHOICES = Patient.SexAtBirth.choices
PATIENT_PREFERRED_CONTACT_CHOICES = Patient.PreferredContact.choices
ALLERGY_SEVERITY_CHOICES = Allergy.Severity.choices
MEDICAL_HISTORY_KIND_CHOICES = MedicalHistory.Kind.choices
ENCOUNTER_KIND_CHOICES = Encounter.Kind.choices
ENCOUNTER_STATUS_CHOICES = Encounter.Status.choices


__all__ = [
    "ALLERGY_SEVERITY_CHOICES",
    "ENCOUNTER_KIND_CHOICES",
    "ENCOUNTER_STATUS_CHOICES",
    "MEDICAL_HISTORY_KIND_CHOICES",
    "PATIENT_PREFERRED_CONTACT_CHOICES",
    "PATIENT_SEX_AT_BIRTH_CHOICES",
    "PATIENT_SOURCE_CHOICES",
    "PATIENT_STATUS_CHOICES",
    "Allergy",
    "Encounter",
    "MedicalHistory",
    "Patient",
    "PatientCounter",
    "PatientMergeLog",
]
