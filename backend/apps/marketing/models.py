"""Marketing persistence models (T132).

Three tables compose existing messaging primitives into campaigns:

1. :class:`Segment` -- a named predicate over a small, non-clinical field
   allowlist (status, last_visit_days, recall_due, plan_status, location,
   source). The predicate is stored as JSON and validated on save; clinical
   notes and diagnoses can never be selected.
2. :class:`Campaign` -- one send of one approved template to one segment on one
   channel. It carries the approval workflow state (``approved_by`` /
   ``approved_at``) and the per-clinic daily send cap. A campaign is activated
   only after a *second* approver (``approved_by != created_by``).
3. :class:`CampaignRecipient` -- one row per patient at activation. The row
   records whether the send happened, was skipped (with the dispatcher's reason)
   or failed, and links the resulting ``messaging.Message``. ``(campaign,
   patient)`` is unique, so resuming a paused campaign can never duplicate an
   already-processed patient.

Every tenant table carries ``clinic_id``; no model stores a message body, a raw
address or a clinical field.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.core.tenancy import ClinicScopedModel
from apps.messaging.models import Consent


class Segment(ClinicScopedModel):
    """A named, non-clinical predicate over the patient base."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    name = models.CharField(max_length=255)
    # ``{"all": [{"field": "last_visit_days", "op": "gte", "value": 180}]}``.
    # Validated against the allowlist by ``services.segments`` before save.
    predicate_json = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "marketing_segment"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "name"],
                name="uniq_segment_clinic_name",
            ),
        ]
        indexes = [
            models.Index(fields=["clinic", "is_active"], name="segment_clinic_active_idx"),
        ]
        verbose_name = "Segment"
        verbose_name_plural = "Segments"

    def __str__(self) -> str:
        return self.name


class Campaign(ClinicScopedModel):
    """One marketing send for one segment and channel."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_APPROVAL = "pending_approval", "Pending approval"
        APPROVED = "approved", "Approved"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    name = models.CharField(max_length=255)
    segment = models.ForeignKey(
        Segment,
        on_delete=models.PROTECT,
        related_name="campaigns",
    )
    template = models.ForeignKey(
        "messaging.MessageTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    channel = models.CharField(max_length=16, choices=Consent.Channel.choices)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.DRAFT
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    #: Maximum sends per clinic-local day. ``0`` means unlimited.
    daily_cap = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "marketing_campaign"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["clinic", "status"], name="campaign_clinic_status_idx"),
            models.Index(fields=["clinic", "created_at"], name="campaign_clinic_created_idx"),
        ]
        verbose_name = "Campaign"
        verbose_name_plural = "Campaigns"

    def __str__(self) -> str:
        return f"{self.name} ({self.status})"


class CampaignRecipient(ClinicScopedModel):
    """One patient's outcome for one campaign."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="recipients",
    )
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="campaign_recipients",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    #: The dispatcher's skip reason (``no_consent``, ``suppressed``, ...).
    skip_reason = models.CharField(max_length=64, blank=True, default="")
    message = models.ForeignKey(
        "messaging.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "marketing_campaignrecipient"
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "patient"],
                name="uniq_campaign_recipient_patient",
            ),
        ]
        indexes = [
            models.Index(
                fields=["clinic", "campaign", "status"],
                name="recipient_clinic_status_idx",
            ),
            models.Index(
                fields=["clinic", "patient"], name="recipient_clinic_patient_idx"
            ),
        ]
        verbose_name = "Campaign recipient"
        verbose_name_plural = "Campaign recipients"

    def __str__(self) -> str:
        return f"{self.patient_id} {self.status}"


# Stable OpenAPI enum names (SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]).
CAMPAIGN_STATUS_CHOICES = Campaign.Status.choices
CAMPAIGN_RECIPIENT_STATUS_CHOICES = CampaignRecipient.Status.choices


__all__ = [
    "CAMPAIGN_RECIPIENT_STATUS_CHOICES",
    "CAMPAIGN_STATUS_CHOICES",
    "Campaign",
    "CampaignRecipient",
    "Segment",
]
