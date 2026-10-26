"""List filtering and ordering for scheduling resources (T110).

Filters are applied to a queryset that the view already scoped to the request
clinic, so a foreign id can never widen the result set. Date bounds are parsed
strictly; an unparseable value is a field-addressed 400 rather than a silent
full scan.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.core.errors import ApiError

APPOINTMENT_ORDERING: dict[str, tuple[str, ...]] = {
    "start_at": ("start_at", "id"),
    "-start_at": ("-start_at", "id"),
    "created_at": ("created_at", "id"),
    "-created_at": ("-created_at", "id"),
    "updated_at": ("updated_at", "id"),
    "-updated_at": ("-updated_at", "id"),
    "status": ("status", "start_at", "id"),
}
DEFAULT_APPOINTMENT_ORDERING: tuple[str, ...] = ("start_at", "id")

RULE_ORDERING: dict[str, tuple[str, ...]] = {
    "weekday": ("weekday", "start_time", "id"),
    "-weekday": ("-weekday", "start_time", "id"),
    "start_time": ("start_time", "id"),
    "-start_time": ("-start_time", "id"),
}
DEFAULT_RULE_ORDERING: tuple[str, ...] = ("weekday", "start_time", "id")

EXCEPTION_ORDERING: dict[str, tuple[str, ...]] = {
    "date": ("date", "id"),
    "-date": ("-date", "id"),
}
DEFAULT_EXCEPTION_ORDERING: tuple[str, ...] = ("date", "id")


def _validation(field: str, message: str) -> ApiError:
    return ApiError(
        "validation_error",
        "The request could not be processed.",
        status_code=400,
        field_errors={field: [message]},
    )


def parse_bound(value: str | None, field: str, *, end_of_day: bool) -> datetime | None:
    """Parse ``from``/``to`` into an aware datetime (date-only means the day).

    A date-only value covers the whole calendar day in the current timezone:
    ``from`` starts at ``00:00:00`` and ``to`` ends at ``23:59:59.999999``.
    The date is therefore parsed *before* the datetime, because
    :func:`django.utils.dateparse.parse_datetime` happily accepts a bare date
    and silently resolves it to midnight - which used to turn a ``to=2026-10-22``
    bound into ``2026-10-22T00:00`` and hide every appointment of that day.
    """

    raw = (value or "").strip()
    if not raw:
        return None
    day = parse_date(raw)
    if day is not None:
        parsed = datetime.combine(day, time.max if end_of_day else time.min)
    else:
        parsed = parse_datetime(raw)
        if parsed is None:
            raise _validation(field, "enter a valid ISO-8601 date or datetime")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def apply_appointment_filters(queryset: QuerySet, params: Any) -> QuerySet:
    """Apply ``status``/``provider``/``location``/``patient``/``source``/date filters."""

    status = (params.get("status") or "").strip()
    if status:
        queryset = queryset.filter(status=status)

    provider = (params.get("provider") or "").strip()
    if provider:
        queryset = queryset.filter(provider_id=provider)

    location = (params.get("location") or "").strip()
    if location:
        queryset = queryset.filter(location_id=location)

    patient = (params.get("patient") or "").strip()
    if patient:
        queryset = queryset.filter(patient_id=patient)

    source = (params.get("source") or "").strip()
    if source:
        queryset = queryset.filter(source=source)

    raw_from = params.get("from") or params.get("start") or params.get("start_at")
    raw_to = params.get("to") or params.get("end") or params.get("end_at")
    start = parse_bound(raw_from, "from", end_of_day=False)
    end = parse_bound(raw_to, "to", end_of_day=True)
    if start is not None:
        queryset = queryset.filter(end_at__gt=start)
    if end is not None:
        queryset = queryset.filter(start_at__lt=end)
    return queryset


def apply_appointment_ordering(queryset: QuerySet, params: Any) -> QuerySet:
    raw = (params.get("ordering") or "").strip()
    if not raw:
        return queryset.order_by(*DEFAULT_APPOINTMENT_ORDERING)
    if raw not in APPOINTMENT_ORDERING:
        raise _validation(
            "ordering", f"select one of: {', '.join(sorted(APPOINTMENT_ORDERING))}"
        )
    return queryset.order_by(*APPOINTMENT_ORDERING[raw])


def apply_rule_filters(queryset: QuerySet, params: Any) -> QuerySet:
    provider = (params.get("provider") or "").strip()
    if provider:
        queryset = queryset.filter(provider_id=provider)
    location = (params.get("location") or "").strip()
    if location:
        queryset = queryset.filter(location_id=location)
    kind = (params.get("kind") or "").strip()
    if kind:
        queryset = queryset.filter(kind=kind)
    weekday = (params.get("weekday") or "").strip()
    if weekday:
        try:
            queryset = queryset.filter(weekday=int(weekday))
        except (TypeError, ValueError):
            raise _validation("weekday", "enter a value between 0 and 6") from None
    return queryset


def apply_rule_ordering(queryset: QuerySet, params: Any) -> QuerySet:
    raw = (params.get("ordering") or "").strip()
    if not raw:
        return queryset.order_by(*DEFAULT_RULE_ORDERING)
    if raw not in RULE_ORDERING:
        raise _validation("ordering", f"select one of: {', '.join(sorted(RULE_ORDERING))}")
    return queryset.order_by(*RULE_ORDERING[raw])


def apply_exception_filters(queryset: QuerySet, params: Any) -> QuerySet:
    provider = (params.get("provider") or "").strip()
    if provider:
        queryset = queryset.filter(provider_id=provider)
    location = (params.get("location") or "").strip()
    if location:
        queryset = queryset.filter(location_id=location)
    kind = (params.get("kind") or "").strip()
    if kind:
        queryset = queryset.filter(kind=kind)
    raw_from = params.get("from")
    raw_to = params.get("to")
    start = _date_bound(raw_from, "from")
    end = _date_bound(raw_to, "to")
    if start is not None:
        queryset = queryset.filter(date__gte=start)
    if end is not None:
        queryset = queryset.filter(date__lte=end)
    return queryset


def apply_exception_ordering(queryset: QuerySet, params: Any) -> QuerySet:
    raw = (params.get("ordering") or "").strip()
    if not raw:
        return queryset.order_by(*DEFAULT_EXCEPTION_ORDERING)
    if raw not in EXCEPTION_ORDERING:
        raise _validation(
            "ordering", f"select one of: {', '.join(sorted(EXCEPTION_ORDERING))}"
        )
    return queryset.order_by(*EXCEPTION_ORDERING[raw])


def _date_bound(value: str | None, field: str) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    parsed = parse_date(raw)
    if parsed is None:
        raise _validation(field, "enter a valid ISO-8601 date")
    return parsed


__all__ = [
    "APPOINTMENT_ORDERING",
    "apply_appointment_filters",
    "apply_appointment_ordering",
    "apply_exception_filters",
    "apply_exception_ordering",
    "apply_rule_filters",
    "apply_rule_ordering",
    "parse_bound",
]
