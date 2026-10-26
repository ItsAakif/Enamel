"""Clinic tenancy models: clinics, locations, memberships and invitations."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.tenancy import ClinicScopedModel


class Clinic(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=64, unique=True)
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    currency = models.CharField(max_length=3, default="INR")
    locale = models.CharField(max_length=16, default="en-IN")
    country = models.CharField(max_length=2, default="IN")
    phone = models.CharField(max_length=32, blank=True)
    address_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    settings_json = models.JSONField(default=dict, blank=True)
    retention_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "clinics_clinic"
        ordering = ["name"]
        verbose_name = "Clinic"
        verbose_name_plural = "Clinics"

    def __str__(self) -> str:
        return self.name


class Location(ClinicScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    name = models.CharField(max_length=255)
    timezone = models.CharField(max_length=64, blank=True)
    address_json = models.JSONField(default=dict, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "clinics_location"
        ordering = ["name", "id"]
        indexes = [models.Index(fields=["clinic", "is_active"], name="loc_clinic_active_idx")]
        verbose_name = "Location"
        verbose_name_plural = "Locations"

    def __str__(self) -> str:
        return self.name


class Membership(ClinicScopedModel):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        DENTIST = "dentist", "Dentist"
        RECEPTIONIST = "receptionist", "Receptionist"
        ASSISTANT = "assistant", "Assistant"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        INVITED = "invited", "Invited"
        REMOVED = "removed", "Removed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.ASSISTANT)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    permission_overrides = models.JSONField(default=list, blank=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "clinics_membership"
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "user"],
                name="uniq_membership_clinic_user",
            )
        ]
        indexes = [models.Index(fields=["clinic", "status"], name="memb_clinic_status_idx")]

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    def effective_permissions(self) -> frozenset[str]:
        """Role permissions plus grant-only overrides."""

        from .permissions import permissions_for

        return permissions_for(self.role, self.permission_overrides)

    def __str__(self) -> str:
        return f"{self.user_id} @ {self.clinic_id} ({self.role})"


class Provider(ClinicScopedModel):
    """A bookable clinician (T110).

    ``Provider`` is the scheduling-facing identity of a clinic member: the
    schedule, availability rules and appointments all point here, while the
    account/role concerns stay on :class:`Membership`. The link is a OneToOne so
    a membership can never be turned into two providers.

    ``Patient.primary_provider`` intentionally still references ``Membership``;
    migrating that column is a later data-carrying change and is documented on
    the patient model.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    membership = models.OneToOneField(
        Membership,
        on_delete=models.CASCADE,
        related_name="provider",
    )
    display_name = models.CharField(max_length=255)
    specialty = models.CharField(max_length=128, blank=True, default="")
    color = models.CharField(max_length=16, blank=True, default="")
    is_bookable = models.BooleanField(default=True)

    class Meta:
        db_table = "clinics_provider"
        ordering = ["display_name", "id"]
        indexes = [
            models.Index(
                fields=["clinic", "is_bookable"],
                name="provider_clinic_bookable_idx",
            ),
        ]
        verbose_name = "Provider"
        verbose_name_plural = "Providers"

    def __str__(self) -> str:
        return self.display_name


class Invitation(models.Model):
    """A single-use, email-bound invitation to join a clinic.

    The model keeps ``clinic_id`` but intentionally does not use the default
    tenant manager: acceptance resolves the invitation by a global secret
    token before any clinic is known.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=32, choices=Membership.Role.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "clinics_invitation"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["clinic", "email"], name="inv_clinic_email_idx")]

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def __str__(self) -> str:
        return f"{self.email} ({self.clinic_id})"


def default_location_name() -> str:
    return "Main location"


# Stable OpenAPI enum names (SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]).
CLINIC_STATUS_CHOICES = Clinic.Status.choices
MEMBERSHIP_ROLE_CHOICES = Membership.Role.choices
MEMBERSHIP_STATUS_CHOICES = Membership.Status.choices


__all__ = [
    "CLINIC_STATUS_CHOICES",
    "Clinic",
    "Invitation",
    "Location",
    "MEMBERSHIP_ROLE_CHOICES",
    "MEMBERSHIP_STATUS_CHOICES",
    "Membership",
    "Provider",
    "default_location_name",
]
