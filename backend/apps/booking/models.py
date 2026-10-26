"""Public booking persistence models (T112).

The booking app owns the *policy*, the short-lived *reservation hold*, the
patient-facing *reference*, the *consent* records captured at booking time and
the single-use record for signed self-service links. It deliberately owns no
slot engine of its own: availability, appointments and the double-booking guard
all live in ``scheduling`` (see ``services.slots`` and ``services.holds``).

Every model extends :class:`apps.core.tenancy.ClinicScopedModel`, so the tenant
manager, the save guard and (later) RLS apply uniformly.

A reservation hold is a real ``scheduling.Appointment`` in ``status=held`` plus
a ``ReservationHold`` row that carries the phone, the token hash and the expiry.
The two share a OneToOne so the sweeper can always find the appointment behind a
hold. ``BookingReference`` is the human-facing ``BK-XXXXXX`` handle the patient
uses to look up or verify their booking.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from apps.core.tenancy import ClinicScopedModel


class BookingPolicy(ClinicScopedModel):
    """Per-clinic public booking rules (one row per clinic).

    The row is created lazily by :func:`apps.booking.services.policy.get_policy`
    with the documented defaults, so a clinic that never opened public booking
    still has a policy to read.
    """

    class Meta:
        db_table = "booking_bookingpolicy"
        verbose_name = "Booking policy"
        verbose_name_plural = "Booking policies"

    # ``clinic`` is the primary key: a clinic has at most one policy.
    clinic = models.OneToOneField(
        "clinics.Clinic",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="booking_policy",
    )
    lead_time_hours = models.PositiveSmallIntegerField(default=2)
    window_days = models.PositiveSmallIntegerField(default=31)
    hold_minutes = models.PositiveSmallIntegerField(default=10)
    cancel_cutoff_hours = models.PositiveSmallIntegerField(default=24)
    reschedule_cutoff_hours = models.PositiveSmallIntegerField(default=24)
    require_email_verification = models.BooleanField(default=False)
    allow_online_reschedule = models.BooleanField(default=True)
    max_future_bookings = models.PositiveSmallIntegerField(default=5)
    consent_wording_version = models.CharField(max_length=32, default="2026-01")
    is_public_enabled = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"policy for {self.clinic_id}"


class ReservationHold(ClinicScopedModel):
    """A short-lived claim on one slot, backed by a ``held`` appointment."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CLAIMED = "claimed", "Claimed"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    appointment = models.OneToOneField(
        "scheduling.Appointment",
        on_delete=models.CASCADE,
        related_name="reservation_hold",
    )
    provider = models.ForeignKey(
        "clinics.Provider",
        on_delete=models.PROTECT,
        related_name="reservation_holds",
    )
    operatory = models.ForeignKey(
        "scheduling.Operatory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reservation_holds",
    )
    slot_start = models.DateTimeField()
    slot_end = models.DateTimeField()
    # SHA-256 of the opaque hold token (never the token itself).
    token_hash = models.CharField(max_length=64, blank=True, default="")
    phone_e164 = models.CharField(max_length=20, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    idempotency_key = models.CharField(max_length=255, blank=True, default="")
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "booking_reservationhold"
        ordering = ["-created_at", "id"]
        indexes = [
            models.Index(
                fields=["clinic", "status", "expires_at"],
                name="hold_clinic_status_exp_idx",
            ),
            models.Index(fields=["clinic", "slot_start"], name="hold_clinic_start_idx"),
        ]
        verbose_name = "Reservation hold"
        verbose_name_plural = "Reservation holds"

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    def __str__(self) -> str:
        return f"hold {self.status} @ {self.slot_start:%Y-%m-%d %H:%M}"


class BookingReference(ClinicScopedModel):
    """The patient-facing ``BK-XXXXXX`` handle for one online booking."""

    class Source(models.TextChoices):
        ONLINE = "online", "Online"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    reference = models.CharField(max_length=16)
    appointment = models.ForeignKey(
        "scheduling.Appointment",
        on_delete=models.CASCADE,
        related_name="booking_references",
    )
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="booking_references",
    )
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        default=Source.ONLINE,
    )
    email_verified_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "booking_bookingreference"
        ordering = ["-created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "reference"],
                name="uniq_booking_reference_clinic",
            ),
            # A concrete idempotency key is unique per clinic; blank keys are
            # free so non-idempotent creates stay possible.
            models.UniqueConstraint(
                fields=["clinic", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uniq_booking_reference_clinic_idem",
            ),
        ]
        indexes = [
            models.Index(fields=["clinic", "created_at"], name="booking_ref_clinic_idx"),
            models.Index(fields=["patient", "created_at"], name="booking_ref_patient_idx"),
        ]
        verbose_name = "Booking reference"
        verbose_name_plural = "Booking references"

    def __str__(self) -> str:
        return f"{self.reference} ({self.clinic_id})"


class BookingConsent(ClinicScopedModel):
    """One consent decision captured at booking time.

    Consent is a record, not a boolean: the purpose, the wording version and the
    timestamp are all stored so the clinic can prove what was agreed and when.
    """

    class Purpose(models.TextChoices):
        BOOKING_TERMS = "booking_terms", "Booking terms"
        PRIVACY = "privacy", "Privacy"
        MARKETING = "marketing", "Marketing"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="booking_consents",
    )
    reference = models.ForeignKey(
        BookingReference,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consents",
    )
    purpose = models.CharField(max_length=32, choices=Purpose.choices)
    wording_version = models.CharField(max_length=32)
    granted = models.BooleanField(default=False)
    captured_at = models.DateTimeField(default=timezone.now)
    # Salted SHA-256 of the request IP (never the raw address).
    ip_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "booking_bookingconsent"
        ordering = ["purpose", "id"]
        indexes = [
            models.Index(fields=["clinic", "patient"], name="consent_clinic_patient_idx"),
            models.Index(
                fields=["clinic", "purpose", "captured_at"],
                name="consent_clinic_purpose_idx",
            ),
        ]
        verbose_name = "Booking consent"
        verbose_name_plural = "Booking consents"

    def __str__(self) -> str:
        return f"{self.purpose}={'granted' if self.granted else 'declined'}"


class SignedLinkUse(ClinicScopedModel):
    """Marks one signed self-service link as consumed (single use).

    The token itself is never stored: only its SHA-256. A mutation (cancel or
    reschedule) inserts a row here, so a replayed token is rejected with 410.
    """

    class Purpose(models.TextChoices):
        CANCEL = "cancel", "Cancel"
        RESCHEDULE = "reschedule", "Reschedule"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    token_hash = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    appointment = models.ForeignKey(
        "scheduling.Appointment",
        on_delete=models.CASCADE,
        related_name="signed_link_uses",
    )
    used_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "booking_signedlinkuse"
        ordering = ["-used_at", "id"]
        verbose_name = "Signed link use"
        verbose_name_plural = "Signed link uses"

    def __str__(self) -> str:
        return f"{self.purpose} {self.token_hash[:12]}"


# Stable OpenAPI enum names (SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]).
RESERVATION_HOLD_STATUS_CHOICES = ReservationHold.Status.choices
BOOKING_REFERENCE_SOURCE_CHOICES = BookingReference.Source.choices
BOOKING_CONSENT_PURPOSE_CHOICES = BookingConsent.Purpose.choices
SIGNED_LINK_PURPOSE_CHOICES = SignedLinkUse.Purpose.choices


__all__ = [
    "BOOKING_CONSENT_PURPOSE_CHOICES",
    "BOOKING_REFERENCE_SOURCE_CHOICES",
    "RESERVATION_HOLD_STATUS_CHOICES",
    "SIGNED_LINK_PURPOSE_CHOICES",
    "BookingConsent",
    "BookingPolicy",
    "BookingReference",
    "ReservationHold",
    "SignedLinkUse",
]
