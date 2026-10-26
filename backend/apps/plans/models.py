"""Treatment plan, consent and document models (T117).

Five ideas shape the schema:

1. **A plan is a snapshot of pricing.** ``TreatmentPlan`` carries the summed
   ``subtotal/discount/tax/total`` minor units and every ``PlanItem`` carries its
   own already-rounded ``line_total_minor``. The server always recomputes both
   from the item inputs; a client-supplied total is never trusted.
2. **Item status is a small state machine.** ``PlanItem.status`` moves
   proposed -> accepted -> completed (or declined); completing an item may flip
   the linked odontogram ``ToothState`` to ``completed`` in the same transaction.
3. **Consent templates are versioned in place.** A ``key`` is unique per clinic
   and an edit bumps ``version``; a ``ConsentRecord`` keeps the integer
   ``template_version`` it was signed against, so editing the template never
   rewrites what a patient actually agreed to.
4. **A signature binds a snapshot.** ``ConsentRecord`` stores the signer, the
   relationship, the timestamp, a salted IP hash, the user agent, the signature
   type and the SHA-256 of the signed artifact. ``drawn``/``typed`` signatures
   are explicitly *not* claimed to be a legally compliant e-signature.
5. **Documents are content-addressed.** The same bytes are stored once under
   ``clinic/<id>/patient/<id>/<sha256>`` and every download is mediated by a
   short-lived signed URL issued only after an authorization check.

``Document`` and ``ConsentRecord`` reference each other (a signature may attach
an uploaded artifact); both foreign keys are nullable so the cycle is safe.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.tenancy import ClinicScopedModel


class TreatmentPlan(ClinicScopedModel):
    """A priced course of treatment proposed to one patient."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PRESENTED = "presented", "Presented"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="treatment_plans",
    )
    provider = models.ForeignKey(
        "clinics.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    title = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    currency = models.CharField(max_length=3, default="INR")
    subtotal_minor = models.BigIntegerField(default=0)
    discount_minor = models.BigIntegerField(default=0)
    tax_minor = models.BigIntegerField(default=0)
    total_minor = models.BigIntegerField(default=0)
    notes = models.TextField(blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    presented_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "plans_treatmentplan"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["clinic", "patient", "status"],
                name="plan_clinic_pat_status_idx",
            ),
            models.Index(fields=["clinic", "created_at"], name="plan_clinic_created_idx"),
        ]
        verbose_name = "Treatment plan"
        verbose_name_plural = "Treatment plans"

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"


class PlanItem(ClinicScopedModel):
    """One priced procedure inside a plan."""

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        ACCEPTED = "accepted", "Accepted"
        COMPLETED = "completed", "Completed"
        DECLINED = "declined", "Declined"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    plan = models.ForeignKey(
        TreatmentPlan,
        on_delete=models.CASCADE,
        related_name="items",
    )
    # Optional link to the odontogram layer this procedure addresses. The FK is
    # nullable because most line items are not tied to a specific tooth layer.
    tooth_state = models.ForeignKey(
        "charting.ToothState",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="plan_items",
    )
    procedure_code = models.CharField(max_length=64)
    description = models.TextField(blank=True, default="")
    tooth = models.SmallIntegerField(null=True, blank=True)
    # Plain JSON list of FDI surface codes, never a Postgres array.
    surfaces = models.JSONField(default=list, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_price_minor = models.BigIntegerField(default=0)
    discount_minor = models.BigIntegerField(default=0)
    tax_rate_bp = models.PositiveIntegerField(default=0)
    line_total_minor = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PROPOSED
    )
    sequence = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "plans_planitem"
        ordering = ["sequence", "id"]
        indexes = [
            models.Index(fields=["clinic", "plan"], name="plan_item_clinic_plan_idx"),
            models.Index(fields=["plan", "status"], name="plan_item_plan_status_idx"),
        ]
        verbose_name = "Plan item"
        verbose_name_plural = "Plan items"

    def __str__(self) -> str:
        return f"{self.procedure_code} x{self.quantity}"


class ConsentTemplate(ClinicScopedModel):
    """A versioned, locale-aware consent wording for one clinic."""

    class ApprovalState(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    key = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    purpose = models.CharField(max_length=64)
    body = models.TextField()
    locale = models.CharField(max_length=16, default="en")
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    approval_state = models.CharField(
        max_length=16,
        choices=ApprovalState.choices,
        default=ApprovalState.DRAFT,
    )

    class Meta:
        db_table = "plans_consenttemplate"
        ordering = ["key", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "key"],
                name="uniq_consent_template_clinic_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["clinic", "is_active"],
                name="consent_tmpl_clinic_active_idx",
            ),
        ]
        verbose_name = "Consent template"
        verbose_name_plural = "Consent templates"

    def __str__(self) -> str:
        return f"{self.key} v{self.version}"


class Document(ClinicScopedModel):
    """A content-addressed uploaded file.

    ``storage_key`` is ``clinic/<id>/patient/<id>/<sha256>``; identical bytes
    therefore map to one stored object. Rows are per-upload metadata, so the
    same file attached to two patients still keeps two audit trails.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="documents",
    )
    kind = models.CharField(max_length=64, blank=True, default="other")
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=128)
    size_bytes = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64)
    storage_key = models.CharField(max_length=512)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    plan = models.ForeignKey(
        TreatmentPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documents",
    )
    consent = models.ForeignKey(
        "plans.ConsentRecord",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documents",
    )

    class Meta:
        db_table = "plans_document"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["clinic", "patient"], name="doc_clinic_patient_idx"),
            models.Index(fields=["clinic", "sha256"], name="doc_clinic_sha_idx"),
        ]
        verbose_name = "Document"
        verbose_name_plural = "Documents"

    def __str__(self) -> str:
        return f"{self.filename} ({self.sha256[:12]})"


class ConsentRecord(ClinicScopedModel):
    """A signed consent artifact bound to one template version."""

    class SignatureType(models.TextChoices):
        DRAWN = "drawn", "Drawn"
        TYPED = "typed", "Typed"
        EXTERNAL_ESIGN = "external_esign", "External e-signature"

    class Status(models.TextChoices):
        SIGNED = "signed", "Signed"
        REVOKED = "revoked", "Revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="consent_records",
    )
    template = models.ForeignKey(
        ConsentTemplate,
        on_delete=models.PROTECT,
        related_name="records",
    )
    # The version the patient actually agreed to; an edit to the template later
    # never changes this value.
    template_version = models.PositiveIntegerField(default=1)
    signer_name = models.CharField(max_length=255)
    relationship = models.CharField(max_length=64, blank=True, default="")
    signature_type = models.CharField(
        max_length=16,
        choices=SignatureType.choices,
        default=SignatureType.TYPED,
    )
    signature_document = models.ForeignKey(
        Document,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    signed_at = models.DateTimeField(default=timezone.now)
    # Salted SHA-256 of the request IP; the raw address is never stored.
    ip_hash = models.CharField(max_length=64, blank=True, default="")
    user_agent = models.CharField(max_length=255, blank=True, default="")
    document_hash = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SIGNED)

    class Meta:
        db_table = "plans_consentrecord"
        ordering = ["-signed_at", "-id"]
        indexes = [
            models.Index(
                fields=["clinic", "patient", "status"],
                name="consent_rec_clinic_pat_idx",
            ),
            models.Index(
                fields=["clinic", "template"],
                name="consent_rec_clinic_tmpl_idx",
            ),
        ]
        verbose_name = "Consent record"
        verbose_name_plural = "Consent records"

    def __str__(self) -> str:
        return f"{self.template_id} v{self.template_version} ({self.signer_name})"


# Stable OpenAPI enum names (SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]).
TREATMENT_PLAN_STATUS_CHOICES = TreatmentPlan.Status.choices
PLAN_ITEM_STATUS_CHOICES = PlanItem.Status.choices
CONSENT_TEMPLATE_APPROVAL_STATE_CHOICES = ConsentTemplate.ApprovalState.choices
CONSENT_SIGNATURE_TYPE_CHOICES = ConsentRecord.SignatureType.choices
CONSENT_RECORD_STATUS_CHOICES = ConsentRecord.Status.choices


__all__ = [
    "CONSENT_RECORD_STATUS_CHOICES",
    "CONSENT_SIGNATURE_TYPE_CHOICES",
    "CONSENT_TEMPLATE_APPROVAL_STATE_CHOICES",
    "PLAN_ITEM_STATUS_CHOICES",
    "TREATMENT_PLAN_STATUS_CHOICES",
    "ConsentRecord",
    "ConsentTemplate",
    "Document",
    "PlanItem",
    "TreatmentPlan",
]
