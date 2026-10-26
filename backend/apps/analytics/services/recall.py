"""Recall and reminder-delivery metrics (T133).

Recall rows are counted by the timestamp that belongs to each metric:

* **due** -- ``RecallStatus.due_at`` inside the window with status ``due``;
* **completed** -- ``last_completed_at`` inside the window with status
  ``completed``.

Reminder delivery is read from the messaging log, which never stores a body or
a raw address (``messaging.Message``):

* **sent** -- ``sent_at`` inside the window;
* **delivered** -- ``delivered_at`` inside the window;
* **failed** -- created inside the window with status ``failed`` or ``dead``
  (a retry that eventually succeeded is counted as sent/delivered, not failed).
"""

from __future__ import annotations

from typing import Any

from django.db.models import Count

from apps.messaging.models import Message, RecallStatus

from ..metrics import resolve_range
from ..registry import payload, register


def _recall_queryset(clinic, from_date, to_date):
    start, end = resolve_range(clinic, from_date, to_date)
    return RecallStatus.all_objects.filter(clinic=clinic, due_at__gte=start, due_at__lt=end)


def _message_queryset(clinic, from_date, to_date):
    start, end = resolve_range(clinic, from_date, to_date)
    return Message.all_objects.filter(clinic=clinic, created_at__gte=start, created_at__lt=end)


def _count(key: str, count: int):
    return payload(key, value=count, numerator_value=count, denominator_value=1)


def due(clinic, from_date, to_date, location=None):
    count = _recall_queryset(clinic, from_date, to_date).filter(
        status=RecallStatus.Status.DUE
    ).count()
    return _count("recall.due", count)


def completed(clinic, from_date, to_date, location=None):
    start, end = resolve_range(clinic, from_date, to_date)
    count = RecallStatus.all_objects.filter(
        clinic=clinic,
        status=RecallStatus.Status.COMPLETED,
        last_completed_at__gte=start,
        last_completed_at__lt=end,
    ).count()
    return _count("recall.completed", count)


def completion_rate(clinic, from_date, to_date, location=None):
    due_count = due(clinic, from_date, to_date)["value"]
    completed_count = completed(clinic, from_date, to_date)["value"]
    value = round(completed_count / due_count, 4) if due_count > 0 else 0.0
    return payload(
        "recall.completion_rate",
        value=value,
        numerator_value=completed_count,
        denominator_value=due_count,
    )


def reminders_sent(clinic, from_date, to_date, location=None):
    start, end = resolve_range(clinic, from_date, to_date)
    count = Message.all_objects.filter(
        clinic=clinic, sent_at__gte=start, sent_at__lt=end
    ).count()
    return _count("reminders.sent", count)


def reminders_delivered(clinic, from_date, to_date, location=None):
    start, end = resolve_range(clinic, from_date, to_date)
    count = Message.all_objects.filter(
        clinic=clinic, delivered_at__gte=start, delivered_at__lt=end
    ).count()
    return _count("reminders.delivered", count)


def reminders_failed(clinic, from_date, to_date, location=None):
    count = _message_queryset(clinic, from_date, to_date).filter(
        status__in=(Message.Status.FAILED, Message.Status.DEAD)
    ).count()
    return _count("reminders.failed", count)


def delivery_rate(clinic, from_date, to_date, location=None):
    sent = reminders_sent(clinic, from_date, to_date)["value"]
    delivered = reminders_delivered(clinic, from_date, to_date)["value"]
    value = round(delivered / sent, 4) if sent > 0 else 0.0
    return payload(
        "reminders.delivery_rate",
        value=value,
        numerator_value=delivered,
        denominator_value=sent,
    )


def by_status(clinic, from_date, to_date, location=None) -> dict[str, int]:
    """Recall rows due in the window, grouped by their current status."""

    rows = (
        _recall_queryset(clinic, from_date, to_date)
        .values("status")
        .annotate(total=Count("id"))
    )
    return {str(row["status"]): int(row["total"]) for row in rows}


def report(clinic, from_date, to_date, location=None) -> dict[str, Any]:
    from ..metrics import range_payload

    return {
        "range": range_payload(clinic, from_date, to_date, location=location),
        "metrics": [
            due(clinic, from_date, to_date),
            completed(clinic, from_date, to_date),
            completion_rate(clinic, from_date, to_date),
            reminders_sent(clinic, from_date, to_date),
            reminders_delivered(clinic, from_date, to_date),
            reminders_failed(clinic, from_date, to_date),
            delivery_rate(clinic, from_date, to_date),
        ],
        "by_status": by_status(clinic, from_date, to_date),
        "definition": {
            "recall.due": "RecallStatus.due_at in window and status due",
            "recall.completed": "last_completed_at in window and status completed",
            "reminders": "messaging.Message timestamps; no body or address is read",
        },
    }


def register_metrics() -> None:
    register("recall.due", due)
    register("recall.completed", completed)
    register("recall.completion_rate", completion_rate)
    register("reminders.sent", reminders_sent)
    register("reminders.delivered", reminders_delivered)
    register("reminders.failed", reminders_failed)
    register("reminders.delivery_rate", delivery_rate)


__all__ = [
    "by_status",
    "completed",
    "completion_rate",
    "delivery_rate",
    "due",
    "register_metrics",
    "reminders_delivered",
    "reminders_failed",
    "reminders_sent",
    "report",
]
