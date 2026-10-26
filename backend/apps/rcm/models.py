"""Insurance and RCM models (T134).

Model-first, adapter-second (ARCHITECTURE.md section 17). The module is disabled
by default (``RCM_ENABLED=false``); the models exist so a clinic that needs
US-style insurance can turn the surface on without a schema change.

Tenant tables (every row carries ``clinic_id``):

* :class:`Payer` -- an insurance payer a clinic contracts with.
* :class:`InsurancePolicy` -- a patient's cover with one payer, valid for a
  date range. Policy resolution for a service date reads ``valid_from`` /
  ``valid_to`` and ``is_active``.
* :class:`EligibilityCheck` -- one deterministic eligibility probe (success or a
  stored failure with an error code).
* :class:`Claim` -- a claim for one patient, optionally linked to a billing
  invoice and an insurance policy. ``correction_of`` links a corrected claim to
  the denied original, which is never edited.
* :class:`ClaimLine` -- one procedure line (integer minor-unit fee).
* :class:`ClaimEvent` -- the append-only adjudication trail.
* :class:`Remittance` -- an 835-shaped settlement (paid + itemized adjustments).
  ``payer_ref`` is unique, so a replayed remittance is rejected, not posted
  twice.

All monetary values are integer minor units. There is no raw X12 parsing: the
interchange is modelled as JSON.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from apps.core.tenancy import ClinicScopedModel


class Payer(ClinicScopedModel):
    """An insurance payer (one per clinic contract)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=32)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "rcm_payer"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "code"], name="uniq_payer_clinic_code"
            ),
        ]
        indexes = [
            models.Index(fields=["clinic", "is_active"], name="payer_clinic_active_idx"),
        ]
        verbose_name = "Payer"
        verbose_name_plural = "Payers"

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class InsurancePolicy(ClinicScopedModel):
    """A patient's cover with a payer over a validity window."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="insurance_policies",
    )
    payer = models.ForeignKey(
        Payer,
        on_delete=models.PROTECT,
        related_name="policies",
    )
    member_id = models.CharField(max_length=64)
    plan_name = models.CharField(max_length=128, blank=True, default="")
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "rcm_insurancepolicy"
        ordering = ["-valid_from", "-id"]
        indexes = [
            models.Index(
                fields=["clinic", "patient", "is_active"],
                name="policy_clinic_pat_active_idx",
            ),
            models.Index(fields=["clinic", "payer"], name="policy_clinic_payer_idx"),
        ]
        verbose_name = "Insurance policy"
        verbose_name_plural = "Insurance policies"

    def __str__(self) -> str:
        return f"{self.payer_id}:{self.member_id}"


class EligibilityCheck(ClinicScopedModel):
    """One deterministic eligibility probe for a policy and service date."""

    class Status(models.TextChoices):
        ELIGIBLE = "eligible", "Eligible"
        INELIGIBLE = "ineligible", "Ineligible"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    policy = models.ForeignKey(
        InsurancePolicy,
        on_delete=models.CASCADE,
        related_name="eligibility_checks",
    )
    service_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices)
    response_json = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    checked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "rcm_eligibilitycheck"
        ordering = ["-checked_at", "-id"]
        indexes = [
            models.Index(
                fields=["clinic", "policy"], name="elig_clinic_policy_idx"
            ),
            models.Index(fields=["clinic", "status"], name="elig_clinic_status_idx"),
        ]
        verbose_name = "Eligibility check"
        verbose_name_plural = "Eligibility checks"

    def __str__(self) -> str:
        return f"{self.status} @ {self.service_date:%Y-%m-%d}"


class Claim(ClinicScopedModel):
    """A claim for one patient (optionally against one invoice)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        VALIDATED = "validated", "Validated"
        SUBMITTED = "submitted", "Submitted"
        ACCEPTED = "accepted", "Accepted"
        DENIED = "denied", "Denied"
        PAID = "paid", "Paid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.PROTECT,
        related_name="claims",
    )
    invoice = models.ForeignKey(
        "billing.Invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="claims",
    )
    policy = models.ForeignKey(
        InsurancePolicy,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="claims",
    )
    payer = models.ForeignKey(
        Payer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="claims",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT
    )
    diagnosis_codes = models.JSONField(default=list, blank=True)
    total_minor = models.BigIntegerField(default=0)
    external_ref = models.CharField(max_length=128, blank=True, default="")
    submitted_at = models.DateTimeField(null=True, blank=True)
    correction_of = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="corrections",
    )

    class Meta:
        db_table = "rcm_claim"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["clinic", "status"], name="claim_clinic_status_idx"),
            models.Index(fields=["clinic", "patient"], name="claim_clinic_patient_idx"),
            models.Index(fields=["clinic", "created_at"], name="claim_clinic_created_idx"),
            models.Index(fields=["external_ref"], name="claim_external_ref_idx"),
        ]
        verbose_name = "Claim"
        verbose_name_plural = "Claims"

    def __str__(self) -> str:
        return f"claim {self.pk} ({self.status})"


class ClaimLine(ClinicScopedModel):
    """One procedure line on a claim (integer minor-unit fee)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    claim = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    procedure_code = models.CharField(max_length=32)
    tooth = models.SmallIntegerField(null=True, blank=True)
    fee_minor = models.BigIntegerField(default=0)
    units = models.PositiveIntegerField(default=1)
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "rcm_claimline"
        ordering = ["sequence", "id"]
        indexes = [
            models.Index(fields=["clinic", "claim"], name="claimline_clinic_claim_idx"),
        ]
        verbose_name = "Claim line"
        verbose_name_plural = "Claim lines"

    def __str__(self) -> str:
        return f"{self.procedure_code} x{self.units}"


class ClaimEvent(ClinicScopedModel):
    """An append-only adjudication event for one claim."""

    class EventType(models.TextChoices):
        CREATED = "created", "Created"
        VALIDATED = "validated", "Validated"
        SUBMITTED = "submitted", "Submitted"
        ACCEPTED = "accepted", "Accepted"
        DENIED = "denied", "Denied"
        REMITTANCE = "remittance", "Remittance"
        CORRECTION = "correction", "Correction"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    claim = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    payload_json = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "rcm_claimevent"
        ordering = ["at", "id"]
        indexes = [
            models.Index(fields=["clinic", "claim", "at"], name="claimevent_claim_at_idx"),
        ]
        verbose_name = "Claim event"
        verbose_name_plural = "Claim events"

    def __str__(self) -> str:
        return f"{self.event_type} @ {self.at:%Y-%m-%d}"


class Remittance(ClinicScopedModel):
    """An 835-shaped settlement for one claim (paid + itemized adjustments)."""

    class Status(models.TextChoices):
        POSTED = "posted", "Posted"
        DENIED = "denied", "Denied"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    claim = models.ForeignKey(
        Claim,
        on_delete=models.PROTECT,
        related_name="remittances",
    )
    #: The payer's remittance reference. Globally unique, so a replay is
    #: rejected instead of double-posting the payment.
    payer_ref = models.CharField(max_length=128, unique=True)
    paid_minor = models.BigIntegerField(default=0)
    adjustment_minor = models.BigIntegerField(default=0)
    #: Itemized contractual adjustments: ``[{"code": "CO-45", "amount_minor": 200}]``.
    adjustments_json = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.POSTED
    )
    posted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "rcm_remittance"
        ordering = ["-posted_at", "-id"]
        indexes = [
            models.Index(fields=["clinic", "claim"], name="remit_clinic_claim_idx"),
            models.Index(fields=["clinic", "status"], name="remit_clinic_status_idx"),
        ]
        verbose_name = "Remittance"
        verbose_name_plural = "Remittances"

    def __str__(self) -> str:
        return f"{self.payer_ref} ({self.status})"


# Stable OpenAPI enum names (SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]).
ELIGIBILITY_STATUS_CHOICES = EligibilityCheck.Status.choices
CLAIM_STATUS_CHOICES = Claim.Status.choices
CLAIM_EVENT_TYPE_CHOICES = ClaimEvent.EventType.choices
REMITTANCE_STATUS_CHOICES = Remittance.Status.choices


__all__ = [
    "CLAIM_EVENT_TYPE_CHOICES",
    "CLAIM_STATUS_CHOICES",
    "ELIGIBILITY_STATUS_CHOICES",
    "REMITTANCE_STATUS_CHOICES",
    "Claim",
    "ClaimEvent",
    "ClaimLine",
    "EligibilityCheck",
    "InsurancePolicy",
    "Payer",
    "Remittance",
]
