"""Waitlist offer/claim metrics (T133).

All three counts are scoped by ``WaitlistOffer.created_at`` inside the window,
so "offers", "claims" and "expiries" describe the same cohort of offers. A claim
counted here was therefore offered inside the window, which is what makes the
claim rate meaningful.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Count

from apps.scheduling.models import WaitlistEntry, WaitlistOffer

from ..metrics import resolve_range
from ..registry import payload, register


def _offer_queryset(clinic, from_date, to_date):
    start, end = resolve_range(clinic, from_date, to_date)
    return WaitlistOffer.all_objects.filter(
        clinic=clinic, created_at__gte=start, created_at__lt=end
    )


def _count(key: str, count: int):
    return payload(key, value=count, numerator_value=count, denominator_value=1)


def offers(clinic, from_date, to_date, location=None):
    return _count("waitlist.offers", _offer_queryset(clinic, from_date, to_date).count())


def claims(clinic, from_date, to_date, location=None):
    count = _offer_queryset(clinic, from_date, to_date).filter(
        status=WaitlistOffer.Status.CLAIMED
    ).count()
    return _count("waitlist.claims", count)


def expired(clinic, from_date, to_date, location=None):
    count = _offer_queryset(clinic, from_date, to_date).filter(
        status=WaitlistOffer.Status.EXPIRED
    ).count()
    return _count("waitlist.expired", count)


def claim_rate(clinic, from_date, to_date, location=None):
    offered = offers(clinic, from_date, to_date)["value"]
    claimed = claims(clinic, from_date, to_date)["value"]
    value = round(claimed / offered, 4) if offered > 0 else 0.0
    return payload(
        "waitlist.claim_rate",
        value=value,
        numerator_value=claimed,
        denominator_value=offered,
    )


def by_status(clinic, from_date, to_date, location=None) -> dict[str, int]:
    """Offers created in the window grouped by their current status."""

    rows = (
        _offer_queryset(clinic, from_date, to_date)
        .values("status")
        .annotate(total=Count("id"))
    )
    return {str(row["status"]): int(row["total"]) for row in rows}


def entries_by_status(clinic, from_date, to_date, location=None) -> dict[str, int]:
    """Waitlist entries created in the window grouped by status."""

    start, end = resolve_range(clinic, from_date, to_date)
    rows = (
        WaitlistEntry.all_objects.filter(
            clinic=clinic, created_at__gte=start, created_at__lt=end
        )
        .values("status")
        .annotate(total=Count("id"))
    )
    return {str(row["status"]): int(row["total"]) for row in rows}


def report(clinic, from_date, to_date, location=None) -> dict[str, Any]:
    from ..metrics import range_payload

    return {
        "range": range_payload(clinic, from_date, to_date, location=location),
        "metrics": [
            offers(clinic, from_date, to_date),
            claims(clinic, from_date, to_date),
            expired(clinic, from_date, to_date),
            claim_rate(clinic, from_date, to_date),
        ],
        "by_status": by_status(clinic, from_date, to_date),
        "entries_by_status": entries_by_status(clinic, from_date, to_date),
        "definition": {
            "cohort": "WaitlistOffer.created_at inside the window",
            "claim_rate": "claimed offers / offers created in the window",
        },
    }


def register_metrics() -> None:
    register("waitlist.offers", offers)
    register("waitlist.claims", claims)
    register("waitlist.expired", expired)
    register("waitlist.claim_rate", claim_rate)


__all__ = [
    "by_status",
    "claim_rate",
    "claims",
    "entries_by_status",
    "expired",
    "offers",
    "register_metrics",
    "report",
]
