"""Overview composition (T133).

The overview is not a new metric: it is a documented, fixed selection of the
headline metrics from the domain services, plus the two chart series the
dashboard needs. Keeping the selection here means the endpoint and the
``overview`` export can never disagree.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Any

from ..metrics import range_payload
from ..registry import collect
from . import appointments, revenue

#: The headline keys shown on the dashboard. Order is the display order.
OVERVIEW_KEYS = [
    "appointments.total",
    "appointments.completion_rate",
    "appointments.cancellation_rate",
    "appointments.no_show_rate",
    "appointments.utilisation",
    "patients.new",
    "patients.returning",
    "revenue.invoiced_minor",
    "revenue.collected_minor",
    "revenue.outstanding_minor",
    "recall.due",
    "recall.completed",
    "reminders.sent",
    "reminders.failed",
    "waitlist.offers",
    "waitlist.claims",
]


def report(clinic, from_date: date_type, to_date: date_type, location=None) -> dict[str, Any]:
    return {
        "range": range_payload(clinic, from_date, to_date, location=location),
        "metrics": collect(OVERVIEW_KEYS, clinic, from_date, to_date, location),
        "appointments": {
            "by_status": appointments.by_status(clinic, from_date, to_date, location),
            "patients": appointments.patient_mix(clinic, from_date, to_date, location),
            "series": appointments.series(clinic, from_date, to_date, location),
        },
        "revenue": {
            "by_currency": {
                "invoiced_minor": revenue.invoiced_by_currency(
                    clinic, from_date, to_date
                ),
                "collected_minor": revenue.collected_by_currency(
                    clinic, from_date, to_date
                ),
                "outstanding_minor": revenue.outstanding_by_currency(
                    clinic, from_date, to_date
                ),
            },
            "series": revenue.series(clinic, from_date, to_date, location),
        },
        "definition": {
            "overview": "headline metrics selected in analytics.services.overview",
            "series": "per-day points in the clinic timezone, from the API only",
        },
    }


def register_metrics() -> None:
    """The overview adds no metrics of its own."""


__all__ = ["OVERVIEW_KEYS", "register_metrics", "report"]
