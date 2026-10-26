"""Remittance posting and reconciliation (T134).

Posting a remittance does two things atomically:

1. records the payer's payment against the linked billing invoice through the
   billing app's ``record_payment`` service (the owning app's write path), and
2. stores the itemized adjustments on the remittance and updates the claim.

``payer_ref`` is unique, so a replayed remittance is rejected with 409 and the
invoice is never credited twice. A denied remittance does not credit anything;
it marks the claim denied and creates a correction claim.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing.models import Invoice, Payment
from apps.billing.services import payments as payment_service
from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.tenancy import with_clinic

from ..models import Claim, ClaimEvent, Remittance
from . import claims as claim_service

_ADJUDICATED = (Claim.Status.PAID, Claim.Status.DENIED)


def _normalise_adjustments(adjustments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []
    for item in adjustments or []:
        normalised.append(
            {
                "code": str(item.get("code") or ""),
                "amount_minor": int(item.get("amount_minor", 0)),
                "description": str(item.get("description") or ""),
            }
        )
    return normalised


def post(
    clinic: Any,
    *,
    claim: Claim,
    payer_ref: str,
    paid_minor: int = 0,
    adjustments: list[dict[str, Any]] | None = None,
    status: str = Remittance.Status.POSTED,
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> Remittance:
    """Post one remittance for ``claim`` (idempotent by ``payer_ref``)."""

    reference = str(payer_ref or "").strip()
    if not reference:
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={"payer_ref": ["This field is required."]},
        )
    if int(paid_minor) < 0:
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={"paid_minor": ["must not be negative"]},
        )

    itemised = _normalise_adjustments(adjustments)
    adjustment_minor = sum(item["amount_minor"] for item in itemised)
    moment = now or timezone.now()

    with transaction.atomic():
        with with_clinic(clinic):
            if Remittance.all_objects.filter(payer_ref=reference).exists():
                raise ApiError(
                    "conflict",
                    "A remittance with this payer reference already exists.",
                    status_code=409,
                    details={"payer_ref": reference},
                )

            locked = Claim.all_objects.select_for_update().get(pk=claim.pk)
            try:
                remittance = Remittance.all_objects.create(
                    clinic=clinic,
                    claim=locked,
                    payer_ref=reference,
                    paid_minor=int(paid_minor),
                    adjustment_minor=adjustment_minor,
                    adjustments_json=itemised,
                    status=status,
                    posted_at=moment,
                )
            except IntegrityError as exc:  # pragma: no cover - race guard
                raise ApiError(
                    "conflict",
                    "A remittance with this payer reference already exists.",
                    status_code=409,
                    details={"payer_ref": reference},
                ) from exc

            if status == Remittance.Status.DENIED:
                claim_service.mark_denied(
                    locked,
                    reason=f"remittance {reference}",
                    actor=actor,
                    request=request,
                    now=moment,
                )
            else:
                if int(paid_minor) > 0 and locked.invoice_id:
                    invoice = Invoice.all_objects.get(pk=locked.invoice_id)
                    payment_service.record_payment(
                        invoice,
                        int(paid_minor),
                        method=Payment.Method.BANK,
                        gateway="rcm",
                        gateway_payment_id=reference,
                        idempotency_key=f"rcm:{reference}",
                        actor=actor,
                        request=request,
                        now=moment,
                    )
                if locked.status not in _ADJUDICATED:
                    settled = int(paid_minor) + adjustment_minor
                    locked.status = (
                        Claim.Status.PAID
                        if settled >= locked.total_minor
                        else Claim.Status.ACCEPTED
                    )
                    locked.save(update_fields=["status", "updated_at"])
                ClaimEvent.all_objects.create(
                    clinic=clinic,
                    claim=locked,
                    event_type=ClaimEvent.EventType.REMITTANCE,
                    payload_json={
                        "payer_ref": reference,
                        "paid_minor": int(paid_minor),
                        "adjustment_minor": adjustment_minor,
                    },
                    at=moment,
                )

            record_audit(
                "rcm.remittance_posted",
                entity=remittance,
                after={
                    "payer_ref": reference,
                    "claim_id": str(locked.pk),
                    "paid_minor": int(paid_minor),
                    "adjustment_minor": adjustment_minor,
                    "status": status,
                },
                actor=actor,
                request=request,
            )
    return remittance


__all__ = ["post"]
