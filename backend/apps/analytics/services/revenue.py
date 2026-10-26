"""Revenue metrics (T133).

Window semantics per document:

* **Invoiced** and **outstanding** use ``Invoice.issued_at`` (the moment the
  clinic recognised the charge). Draft and void invoices are excluded: they are
  not revenue.
* **Collected** uses ``Payment.captured_at`` for ``succeeded`` payments. Pending
  and failed attempts are not money.
* **Refunds are not netted out** of collected here; a refund is a separate
  liability record and netting it silently would make "collected" mean
  something other than what the payment log says.

Money is integer minor units plus an ISO-4217 currency (ARCHITECTURE.md
section 9), so every metric carries a ``currency`` and no cross-currency total
is ever produced.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Sum

from apps.billing.models import Invoice, Payment

from ..metrics import day_series_bounds, resolve_range
from ..registry import payload, register

OPEN_INVOICE_STATUSES = (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID)


def _invoice_queryset(clinic, from_date, to_date, location=None):
    start, end = resolve_range(clinic, from_date, to_date)
    # Invoices are not location-scoped (billing has no location column), so a
    # location filter is a documented no-op for revenue.
    return Invoice.all_objects.filter(
        clinic=clinic, issued_at__gte=start, issued_at__lt=end
    )


def _payment_queryset(clinic, from_date, to_date, location=None):
    start, end = resolve_range(clinic, from_date, to_date)
    return Payment.all_objects.filter(
        clinic=clinic,
        status=Payment.Status.SUCCEEDED,
        captured_at__gte=start,
        captured_at__lt=end,
    )


def _sum_by_currency(queryset, field: str) -> dict[str, int]:
    rows = queryset.values("currency").annotate(total=Sum(field))
    return {
        str(row["currency"]): int(row["total"] or 0)
        for row in rows
    }


def invoiced_by_currency(clinic, from_date, to_date, location=None) -> dict[str, int]:
    return _sum_by_currency(_invoice_queryset(clinic, from_date, to_date), "total_minor")


def collected_by_currency(clinic, from_date, to_date, location=None) -> dict[str, int]:
    return _sum_by_currency(_payment_queryset(clinic, from_date, to_date), "amount_minor")


def outstanding_by_currency(clinic, from_date, to_date, location=None) -> dict[str, int]:
    queryset = _invoice_queryset(clinic, from_date, to_date).filter(
        status__in=OPEN_INVOICE_STATUSES
    )
    return _sum_by_currency(queryset, "balance_minor")


def _currency_payloads(key: str, amounts: dict[str, int]) -> list[dict[str, Any]]:
    if not amounts:
        # Zero-state: report the metric once with no currency and a zero value
        # rather than inventing a currency or returning null.
        return [
            payload(key, value=0, numerator_value=0, denominator_value=1, currency=None)
        ]
    return [
        payload(key, value=amount, numerator_value=amount, denominator_value=1, currency=currency)
        for currency, amount in sorted(amounts.items())
    ]


def invoiced(clinic, from_date, to_date, location=None):
    return _currency_payloads(
        "revenue.invoiced_minor", invoiced_by_currency(clinic, from_date, to_date)
    )


def collected(clinic, from_date, to_date, location=None):
    return _currency_payloads(
        "revenue.collected_minor", collected_by_currency(clinic, from_date, to_date)
    )


def outstanding(clinic, from_date, to_date, location=None):
    return _currency_payloads(
        "revenue.outstanding_minor", outstanding_by_currency(clinic, from_date, to_date)
    )


def collection_rate(clinic, from_date, to_date, location=None):
    invoiced_amounts = invoiced_by_currency(clinic, from_date, to_date)
    collected_amounts = collected_by_currency(clinic, from_date, to_date)
    currencies = sorted(set(invoiced_amounts) | set(collected_amounts))
    if not currencies:
        return [
            payload(
                "revenue.collection_rate",
                value=0.0,
                numerator_value=0,
                denominator_value=0,
                currency=None,
            )
        ]
    results = []
    for currency in currencies:
        numerator = collected_amounts.get(currency, 0)
        denominator = invoiced_amounts.get(currency, 0)
        value = round(numerator / denominator, 4) if denominator > 0 else 0.0
        results.append(
            payload(
                "revenue.collection_rate",
                value=value,
                numerator_value=numerator,
                denominator_value=denominator,
                currency=currency,
            )
        )
    return results


def series(clinic, from_date, to_date, location=None) -> list[dict[str, Any]]:
    """Per-day invoiced and collected totals (chart data)."""

    points: list[dict[str, Any]] = []
    for day, start, end in day_series_bounds(clinic, from_date, to_date):
        invoiced_total = (
            Invoice.all_objects.filter(
                clinic=clinic, issued_at__gte=start, issued_at__lt=end
            ).aggregate(total=Sum("total_minor"))["total"]
            or 0
        )
        collected_total = (
            Payment.all_objects.filter(
                clinic=clinic,
                status=Payment.Status.SUCCEEDED,
                captured_at__gte=start,
                captured_at__lt=end,
            ).aggregate(total=Sum("amount_minor"))["total"]
            or 0
        )
        points.append(
            {
                "date": day.isoformat(),
                "invoiced_minor": int(invoiced_total),
                "collected_minor": int(collected_total),
            }
        )
    return points


def report(clinic, from_date, to_date, location=None) -> dict[str, Any]:
    from ..metrics import range_payload

    metrics: list[dict[str, Any]] = []
    metrics.extend(invoiced(clinic, from_date, to_date, location))
    metrics.extend(collected(clinic, from_date, to_date, location))
    metrics.extend(outstanding(clinic, from_date, to_date, location))
    metrics.extend(collection_rate(clinic, from_date, to_date, location))
    return {
        "range": range_payload(clinic, from_date, to_date, location=location),
        "metrics": metrics,
        "by_currency": {
            "invoiced_minor": invoiced_by_currency(clinic, from_date, to_date),
            "collected_minor": collected_by_currency(clinic, from_date, to_date),
            "outstanding_minor": outstanding_by_currency(clinic, from_date, to_date),
        },
        "series": series(clinic, from_date, to_date, location),
        "definition": {
            "invoiced": "Invoice.issued_at inside the window; draft/void excluded",
            "collected": "Payment.captured_at inside the window; succeeded only",
            "outstanding": "open invoices (issued/partially_paid) issued in the window",
            "refunds": "not netted out of collected",
            "location": "revenue is clinic-scoped; a location filter is a no-op",
        },
    }


def register_metrics() -> None:
    register("revenue.invoiced_minor", invoiced)
    register("revenue.collected_minor", collected)
    register("revenue.outstanding_minor", outstanding)
    register("revenue.collection_rate", collection_rate)


__all__ = [
    "OPEN_INVOICE_STATUSES",
    "collected",
    "collected_by_currency",
    "collection_rate",
    "invoiced",
    "invoiced_by_currency",
    "outstanding",
    "outstanding_by_currency",
    "register_metrics",
    "report",
    "series",
]
