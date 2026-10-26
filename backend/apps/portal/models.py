"""Patient portal persistence models (T121).

The portal is a *separate principal* from clinic staff: it has its own session
table and cookie so a staff session can never authenticate a portal endpoint and
a portal session can never authenticate a staff endpoint.

Both tables are plain ``models.Model`` (not ``ClinicScopedModel``) on purpose:
they are resolved *before* a clinic scope exists. ``PatientSession`` is looked up
by the opaque cookie's SHA-256 with no clinic context, and ``PortalOtpChallenge``
by its id. Both still carry ``clinic_id`` so every resolved object can be
re-checked against the clinic that owns it.

No raw secret is ever stored: the session key and the OTP code are kept only as
SHA-256 hashes, and the contact (phone/email) is stored only as a salted hash.
"""

from __future__ import annotations

import uuid

from django.db import models


class PortalChannel(models.TextChoices):
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"


class PatientSession(models.Model):
    """One authenticated portal session (cookie ``enamel_portal``)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="portal_sessions",
    )
    clinic = models.ForeignKey(
        "clinics.Clinic",
        on_delete=models.CASCADE,
        related_name="portal_sessions",
    )
    #: SHA-256 of the opaque cookie value. The raw key is never stored.
    key_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_seen_at = models.DateTimeField(null=True, blank=True)
    #: Salted SHA-256 of the request IP (never the raw address).
    ip_hash = models.CharField(max_length=64, blank=True, default="")
    user_agent = models.CharField(max_length=255, blank=True, default="")
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "portal_patientsession"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["patient", "-created_at"], name="portal_sess_patient_idx"),
            models.Index(fields=["clinic", "expires_at"], name="portal_sess_clinic_exp_idx"),
        ]
        verbose_name = "Patient session"
        verbose_name_plural = "Patient sessions"

    def __str__(self) -> str:
        return f"portal session {self.patient_id} ({self.clinic_id})"


class PortalOtpChallenge(models.Model):
    """A one-time code challenge for portal sign-in."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="portal_otp_challenges",
    )
    clinic = models.ForeignKey(
        "clinics.Clinic",
        on_delete=models.CASCADE,
        related_name="portal_otp_challenges",
    )
    channel = models.CharField(max_length=8, choices=PortalChannel.choices)
    #: Salted SHA-256 of the normalized contact the code was sent to.
    contact_hash = models.CharField(max_length=64)
    #: Salted SHA-256 of the 6-digit code. The code itself is never stored.
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "portal_portalotpchallenge"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["patient", "-created_at"], name="portal_otp_patient_idx"),
            models.Index(fields=["clinic", "expires_at"], name="portal_otp_clinic_exp_idx"),
        ]
        verbose_name = "Portal OTP challenge"
        verbose_name_plural = "Portal OTP challenges"

    def __str__(self) -> str:
        return f"otp {self.channel} ({self.patient_id})"

    @property
    def is_used(self) -> bool:
        return self.used_at is not None


# Stable OpenAPI enum names (SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]).
PORTAL_CHANNEL_CHOICES = PortalChannel.choices


__all__ = [
    "PORTAL_CHANNEL_CHOICES",
    "PatientSession",
    "PortalChannel",
    "PortalOtpChallenge",
]
