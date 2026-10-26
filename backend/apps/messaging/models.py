"""Messaging persistence models (T114).

The messaging app owns consent, suppression, templates, the delivery log and
dental recall. Four ideas shape the schema:

1. **Consent is a record, not a boolean.** A row keeps the channel, the purpose,
   the wording version, the source, the evidence and the grant/revoke instants,
   so a clinic can prove what a patient agreed to and when. Only one *granted*
   row may exist per ``(patient, channel, purpose)``; revoked rows accumulate as
   history.
2. **Suppression is keyed by a hashed address, never a raw one.** A STOP/HELP/
   bounce event inserts a ``(clinic, channel, address_hash)`` row; the unique
   constraint makes duplicate opt-outs idempotent.
3. **Templates are versioned in place.** A ``key`` is unique per clinic and an
   edit bumps ``version``; every ``Message`` keeps the integer
   ``template_version`` it was rendered with, so a later edit cannot rewrite
   history.
4. **The delivery log never stores a body or a raw address.** A ``Message`` row
   holds the status machine (queued/sent/delivered/failed/skipped/dead), the
   provider id, error code, attempts and the *whitelisted* template context used
   to render it. The rendered body is passed to the adapter and not persisted.

``RecallType``/``RecallStatus`` implement the dental recall interval: the due
date is derived from the last completed encounter/treatment plus the configured
interval in months.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from apps.core.tenancy import ClinicScopedModel


class Consent(ClinicScopedModel):
    """One patient x channel x purpose consent decision."""

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"
        WHATSAPP = "whatsapp", "WhatsApp"
        VOICE = "voice", "Voice"

    class Purpose(models.TextChoices):
        APPOINTMENT_REMINDERS = "appointment_reminders", "Appointment reminders"
        RECALL = "recall", "Recall"
        MARKETING = "marketing", "Marketing"
        TRANSACTIONAL = "transactional", "Transactional"

    class Status(models.TextChoices):
        GRANTED = "granted", "Granted"
        REVOKED = "revoked", "Revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="consents",
    )
    channel = models.CharField(max_length=16, choices=Channel.choices)
    purpose = models.CharField(max_length=32, choices=Purpose.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.GRANTED)
    source = models.CharField(max_length=64, blank=True, default="")
    wording_version = models.CharField(max_length=32, blank=True, default="")
    evidence_json = models.JSONField(default=dict, blank=True)
    granted_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    # Salted SHA-256 of the request IP (never the raw address).
    ip_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "messaging_consent"
        ordering = ["-granted_at", "-id"]
        constraints = [
            # Only one *granted* row per patient/channel/purpose. Revoked rows
            # stay as evidence and fall outside the constraint.
            models.UniqueConstraint(
                fields=["patient", "channel", "purpose"],
                condition=models.Q(status="granted"),
                name="uniq_active_consent_patient_channel_purpose",
            ),
        ]
        indexes = [
            models.Index(
                fields=["clinic", "patient", "status"],
                name="consent_clinic_pat_status_idx",
            ),
            models.Index(
                fields=["clinic", "channel", "purpose"],
                name="consent_clinic_chan_purp_idx",
            ),
        ]
        verbose_name = "Consent"
        verbose_name_plural = "Consents"

    def __str__(self) -> str:
        return f"{self.channel}/{self.purpose}={self.status}"


class Suppression(ClinicScopedModel):
    """A hashed address that must not receive non-transactional messages."""

    class Reason(models.TextChoices):
        STOP = "stop", "Stop"
        HELP = "help", "Help"
        BOUNCE = "bounce", "Bounce"
        COMPLAINT = "complaint", "Complaint"
        MANUAL = "manual", "Manual"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    channel = models.CharField(max_length=16, choices=Consent.Channel.choices)
    # Salted SHA-256 of the address; the raw email/phone is never stored.
    address_hash = models.CharField(max_length=64)
    reason = models.CharField(max_length=16, choices=Reason.choices)

    class Meta:
        db_table = "messaging_suppression"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "channel", "address_hash"],
                name="uniq_suppression_clinic_channel_hash",
            ),
        ]
        indexes = [
            models.Index(
                fields=["clinic", "channel", "address_hash"],
                name="suppression_clinic_hash_idx",
            ),
        ]
        verbose_name = "Suppression"
        verbose_name_plural = "Suppressions"

    def __str__(self) -> str:
        return f"{self.channel}:{self.address_hash[:12]} ({self.reason})"


class MessageTemplate(ClinicScopedModel):
    """A versioned, locale-aware message template with a variable allowlist."""

    class ApprovalStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    key = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    channel = models.CharField(max_length=16, choices=Consent.Channel.choices)
    purpose = models.CharField(max_length=32, choices=Consent.Purpose.choices)
    locale = models.CharField(max_length=16, default="en")
    version = models.PositiveIntegerField(default=1)
    subject = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField()
    # Plain JSON list of allowed ``{variable}`` names (never a Postgres array).
    variables = models.JSONField(default=list, blank=True)
    approval_status = models.CharField(
        max_length=16,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.DRAFT,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "messaging_messagetemplate"
        ordering = ["key", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "key"],
                name="uniq_message_template_clinic_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["clinic", "is_active", "approval_status"],
                name="tmpl_clinic_active_appr_idx",
            ),
        ]
        verbose_name = "Message template"
        verbose_name_plural = "Message templates"

    def __str__(self) -> str:
        return f"{self.key} v{self.version}"


class Message(ClinicScopedModel):
    """One delivery attempt (or a recorded skip) for one patient."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"
        DEAD = "dead", "Dead"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="messages",
    )
    channel = models.CharField(max_length=16, choices=Consent.Channel.choices)
    purpose = models.CharField(max_length=32, choices=Consent.Purpose.choices)
    template = models.ForeignKey(
        MessageTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )
    template_version = models.PositiveIntegerField(default=0)
    # Salted SHA-256 of the destination address; never the raw address.
    to_address_hash = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    reason = models.CharField(max_length=64, blank=True, default="")
    provider = models.CharField(max_length=32, blank=True, default="")
    provider_message_id = models.CharField(max_length=128, blank=True, default="")
    error_code = models.CharField(max_length=64, blank=True, default="")
    cost_minor = models.BigIntegerField(default=0)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    idempotency_key = models.CharField(max_length=255, blank=True, default="")
    # Only the whitelisted template variables are stored, so a deferred or
    # retried send can re-render without persisting a body or a raw address.
    context_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "messaging_message"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uniq_message_clinic_idempotency",
            ),
        ]
        indexes = [
            models.Index(fields=["clinic", "status"], name="message_clinic_status_idx"),
            models.Index(
                fields=["clinic", "patient", "channel"],
                name="message_clinic_pat_chan_idx",
            ),
            models.Index(fields=["provider_message_id"], name="message_provider_msg_idx"),
            models.Index(fields=["clinic", "created_at"], name="message_clinic_created_idx"),
        ]
        verbose_name = "Message"
        verbose_name_plural = "Messages"

    def __str__(self) -> str:
        return f"{self.channel} {self.status} ({self.pk})"


class ProviderEvent(ClinicScopedModel):
    """A de-duplicated provider webhook event for one message."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="provider_events",
    )
    event_id = models.CharField(max_length=128)
    event_type = models.CharField(max_length=32)
    payload_redacted = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "messaging_providerevent"
        ordering = ["-occurred_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "event_id"],
                name="uniq_provider_event_clinic_event",
            ),
        ]
        indexes = [
            models.Index(
                fields=["clinic", "message"],
                name="prov_event_clinic_msg_idx",
            ),
        ]
        verbose_name = "Provider event"
        verbose_name_plural = "Provider events"

    def __str__(self) -> str:
        return f"{self.event_type} {self.event_id}"


class RecallType(ClinicScopedModel):
    """A clinic-configured recall rule (interval + channel + template)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    name = models.CharField(max_length=128)
    interval_months = models.PositiveSmallIntegerField(default=6)
    channel = models.CharField(
        max_length=16,
        choices=Consent.Channel.choices,
        default=Consent.Channel.EMAIL,
    )
    template_key = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "messaging_recalltype"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "name"],
                name="uniq_recall_type_clinic_name",
            ),
        ]
        indexes = [
            models.Index(fields=["clinic", "is_active"], name="recall_type_clinic_active_idx"),
        ]
        verbose_name = "Recall type"
        verbose_name_plural = "Recall types"

    def __str__(self) -> str:
        return f"{self.name} ({self.interval_months}m)"


class RecallStatus(ClinicScopedModel):
    """Per-patient due date for one recall type."""

    class Status(models.TextChoices):
        DUE = "due", "Due"
        SCHEDULED = "scheduled", "Scheduled"
        COMPLETED = "completed", "Completed"
        DISMISSED = "dismissed", "Dismissed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="recall_statuses",
    )
    recall_type = models.ForeignKey(
        RecallType,
        on_delete=models.CASCADE,
        related_name="statuses",
    )
    due_at = models.DateTimeField()
    last_completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DUE)

    class Meta:
        db_table = "messaging_recallstatus"
        ordering = ["due_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["patient", "recall_type"],
                name="uniq_recall_status_patient_type",
            ),
        ]
        indexes = [
            models.Index(
                fields=["clinic", "status", "due_at"],
                name="recall_status_clinic_due_idx",
            ),
        ]
        verbose_name = "Recall status"
        verbose_name_plural = "Recall statuses"

    def __str__(self) -> str:
        return f"{self.recall_type_id} {self.status} @ {self.due_at:%Y-%m-%d}"


# Stable OpenAPI enum names (SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]).
MESSAGING_CHANNEL_CHOICES = Consent.Channel.choices
MESSAGING_PURPOSE_CHOICES = Consent.Purpose.choices
CONSENT_STATUS_CHOICES = Consent.Status.choices
SUPPRESSION_REASON_CHOICES = Suppression.Reason.choices
TEMPLATE_APPROVAL_STATUS_CHOICES = MessageTemplate.ApprovalStatus.choices
MESSAGE_STATUS_CHOICES = Message.Status.choices
RECALL_STATUS_CHOICES = RecallStatus.Status.choices


__all__ = [
    "CONSENT_STATUS_CHOICES",
    "MESSAGING_CHANNEL_CHOICES",
    "MESSAGING_PURPOSE_CHOICES",
    "MESSAGE_STATUS_CHOICES",
    "RECALL_STATUS_CHOICES",
    "SUPPRESSION_REASON_CHOICES",
    "TEMPLATE_APPROVAL_STATUS_CHOICES",
    "Consent",
    "Message",
    "MessageTemplate",
    "ProviderEvent",
    "RecallStatus",
    "RecallType",
    "Suppression",
]
