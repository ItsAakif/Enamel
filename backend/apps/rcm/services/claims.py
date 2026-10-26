"""Claim creation, submission, denial and correction (T134).

Submission is idempotent per claim: a claim that already carries an
``external_ref`` and is past ``validated`` is returned unchanged, so a retried
submit never creates a second submission. A denial never edits the original
claim's content; it creates a *correction* claim that points back through
``correction_of``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.audit import record as record_audit
from apps.core.tenancy import with_clinic

from ..adapters import resolve_adapter
from ..models import Claim, ClaimEvent, ClaimLine
from . import validation

SNAPSHOT_FIELDS = ("status", "total_minor", "external_ref", "payer_id", "policy_id")


def snapshot(claim: Claim) -> dict[str, Any]:
    """Audit projection for a claim (enums and money only)."""

    return {
        "status": claim.status,
        "total_minor": claim.total_minor,
        "external_ref": claim.external_ref,
        "payer_id": str(claim.payer_id) if claim.payer_id else None,
        "policy_id": str(claim.policy_id) if claim.policy_id else None,
        "correction_of_id": (
            str(claim.correction_of_id) if claim.correction_of_id else None
        ),
    }


def _event(
    claim: Claim,
    event_type: str,
    *,
    payload: dict[str, Any] | None = None,
    moment: datetime | None = None,
) -> ClaimEvent:
    return ClaimEvent.all_objects.create(
        clinic=claim.clinic,
        claim=claim,
        event_type=event_type,
        payload_json=payload or {},
        at=moment or timezone.now(),
    )


def _recompute_total(claim: Claim) -> int:
    total = 0
    for line in claim.lines.all():
        total += int(line.fee_minor) * int(line.units or 1)
    claim.total_minor = total
    claim.save(update_fields=["total_minor", "updated_at"])
    return total


def create_claim(
    clinic: Any,
    *,
    patient: Any,
    lines: list[dict[str, Any]],
    policy: Any = None,
    invoice: Any = None,
    diagnosis_codes: list[str] | None = None,
    payer: Any = None,
    status: str = Claim.Status.DRAFT,
    correction_of: Claim | None = None,
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> Claim:
    """Create a claim with its lines and recomputed total."""

    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(clinic):
            resolved_payer = payer or (policy.payer if policy is not None else None)
            claim = Claim.all_objects.create(
                clinic=clinic,
                patient=patient,
                invoice=invoice,
                policy=policy,
                payer=resolved_payer,
                status=status,
                diagnosis_codes=list(diagnosis_codes or []),
                correction_of=correction_of,
            )
            for index, spec in enumerate(lines):
                ClaimLine.all_objects.create(
                    clinic=clinic,
                    claim=claim,
                    procedure_code=spec["procedure_code"],
                    tooth=spec.get("tooth"),
                    fee_minor=int(spec.get("fee_minor", 0)),
                    units=int(spec.get("units", 1) or 1),
                    sequence=spec.get("sequence", index),
                )
            _recompute_total(claim)
            _event(claim, ClaimEvent.EventType.CREATED, payload=snapshot(claim), moment=moment)
            record_audit(
                "rcm.claim_created",
                entity=claim,
                after=snapshot(claim),
                actor=actor,
                request=request,
            )
    return claim


def submit(
    claim: Claim,
    *,
    adapter: Any = None,
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> Claim:
    """Validate and submit ``claim`` once (idempotent on retry)."""

    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(claim.clinic):
            locked = Claim.all_objects.select_for_update().get(pk=claim.pk)
            if locked.external_ref and locked.status in (
                Claim.Status.SUBMITTED,
                Claim.Status.ACCEPTED,
                Claim.Status.PAID,
                Claim.Status.DENIED,
            ):
                # A second submit returns the existing submission.
                return locked

            validation.assert_submittable(locked)

            locked.status = Claim.Status.VALIDATED
            locked.save(update_fields=["status", "updated_at"])
            _event(
                locked,
                ClaimEvent.EventType.VALIDATED,
                payload={"total_minor": locked.total_minor},
                moment=moment,
            )

            chosen = adapter or resolve_adapter()
            result = chosen.submit_claim(locked)
            locked.external_ref = result.external_ref
            locked.status = Claim.Status.SUBMITTED
            locked.submitted_at = moment
            locked.save(
                update_fields=["external_ref", "status", "submitted_at", "updated_at"]
            )
            _event(
                locked,
                ClaimEvent.EventType.SUBMITTED,
                payload={"external_ref": result.external_ref, **result.payload},
                moment=moment,
            )
            record_audit(
                "rcm.claim_submitted",
                entity=locked,
                after=snapshot(locked),
                actor=actor,
                request=request,
            )
    return locked


def create_correction(
    claim: Claim,
    *,
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> Claim:
    """Create a draft correction claim linked to ``claim`` (original untouched)."""

    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(claim.clinic):
            correction = Claim.all_objects.create(
                clinic=claim.clinic,
                patient=claim.patient,
                invoice=claim.invoice,
                policy=claim.policy,
                payer=claim.payer,
                status=Claim.Status.DRAFT,
                diagnosis_codes=list(claim.diagnosis_codes or []),
                correction_of=claim,
            )
            for line in claim.lines.all().order_by("sequence", "id"):
                ClaimLine.all_objects.create(
                    clinic=claim.clinic,
                    claim=correction,
                    procedure_code=line.procedure_code,
                    tooth=line.tooth,
                    fee_minor=line.fee_minor,
                    units=line.units,
                    sequence=line.sequence,
                )
            _recompute_total(correction)
            _event(
                correction,
                ClaimEvent.EventType.CORRECTION,
                payload={"correction_of": str(claim.pk)},
                moment=moment,
            )
            record_audit(
                "rcm.claim_corrected",
                entity=correction,
                after={**snapshot(correction), "correction_of": str(claim.pk)},
                actor=actor,
                request=request,
            )
    return correction


def mark_denied(
    claim: Claim,
    *,
    reason: str = "",
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> tuple[Claim, Claim]:
    """Mark ``claim`` denied and create its correction claim."""

    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(claim.clinic):
            locked = Claim.all_objects.select_for_update().get(pk=claim.pk)
            before = snapshot(locked)
            locked.status = Claim.Status.DENIED
            locked.save(update_fields=["status", "updated_at"])
            _event(
                locked,
                ClaimEvent.EventType.DENIED,
                payload={"reason": reason},
                moment=moment,
            )
            record_audit(
                "rcm.claim_denied",
                entity=locked,
                before=before,
                after=snapshot(locked),
                actor=actor,
                request=request,
            )
            correction = create_correction(
                locked, actor=actor, request=request, now=moment
            )
    return locked, correction


__all__ = [
    "SNAPSHOT_FIELDS",
    "create_claim",
    "create_correction",
    "mark_denied",
    "snapshot",
    "submit",
]
