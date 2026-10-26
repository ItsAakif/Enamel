"""Claim reconciliation report (T134).

Reconciliation is counted from stored rows only: for every claim it sums the
posted remittances and compares the settlement to the billed total. A fully
paid claim has zero drift; a denied claim is excluded from the drift total
because its value moves to the correction claim.

The report never adjusts the books; it reports what is stored so an operator can
act on a non-zero drift.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Sum

from ..models import Claim, Remittance

SETTLED_STATUSES = (Claim.Status.PAID,)


def _claim_row(claim: Claim) -> dict[str, Any]:
    aggregates = Remittance.all_objects.filter(
        claim_id=claim.pk, status=Remittance.Status.POSTED
    ).aggregate(paid=Sum("paid_minor"), adjusted=Sum("adjustment_minor"))
    paid = int(aggregates["paid"] or 0)
    adjusted = int(aggregates["adjusted"] or 0)
    outstanding = int(claim.total_minor) - paid - adjusted
    invoice = claim.invoice
    return {
        "claim_id": str(claim.pk),
        "status": claim.status,
        "total_minor": int(claim.total_minor),
        "paid_minor": paid,
        "adjustment_minor": adjusted,
        "outstanding_minor": outstanding,
        "invoice_id": str(claim.invoice_id) if claim.invoice_id else None,
        "invoice_balance_minor": int(invoice.balance_minor) if invoice else None,
        "drift_minor": outstanding if claim.status in SETTLED_STATUSES else None,
    }


def reconciliation(clinic: Any) -> dict[str, Any]:
    """Return per-claim settlement rows and a drift summary for ``clinic``."""

    claims = (
        Claim.all_objects.filter(clinic=clinic)
        .select_related("invoice")
        .order_by("-created_at", "-id")
    )
    rows = [_claim_row(claim) for claim in claims]
    settled = [row for row in rows if row["drift_minor"] is not None]
    drift = sum(int(row["drift_minor"]) for row in settled)
    return {
        "claims": rows,
        "summary": {
            "claims": len(rows),
            "settled_claims": len(settled),
            "billed_minor": sum(int(row["total_minor"]) for row in rows),
            "paid_minor": sum(int(row["paid_minor"]) for row in rows),
            "adjustment_minor": sum(int(row["adjustment_minor"]) for row in rows),
            "outstanding_minor": sum(int(row["outstanding_minor"]) for row in rows),
            "drift_minor": drift,
        },
        "zero_drift": drift == 0,
    }


__all__ = ["SETTLED_STATUSES", "reconciliation"]
