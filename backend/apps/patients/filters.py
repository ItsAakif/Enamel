"""Patient list filtering, searching and ordering (T106).

Search is clinic-scoped by the queryset it is handed (the tenant manager output),
so a term can never reach another clinic's rows. Four signals are matched, which
is what the API documents: partial first/last/full name, patient number,
normalized phone digits and email.

``icontains`` is used deliberately (not ``istartswith``) because clinicians
search by surname fragments; the accompanying ``pg_trgm`` GIN index added in
``migrations/0002_patient_search_index`` keeps that from degenerating into a
sequential scan.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet

from apps.core.errors import ApiError

from .services.dedup import phone_search_digits

DEFAULT_ORDERING: tuple[str, ...] = ("last_name", "first_name", "id")

ORDERING_CHOICES: dict[str, tuple[str, ...]] = {
    "last_name": ("last_name", "first_name", "id"),
    "-last_name": ("-last_name", "-first_name", "id"),
    "first_name": ("first_name", "last_name", "id"),
    "-first_name": ("-first_name", "-last_name", "id"),
    "number": ("number",),
    "-number": ("-number",),
    "created_at": ("created_at", "id"),
    "-created_at": ("-created_at", "id"),
    "updated_at": ("updated_at", "id"),
    "-updated_at": ("-updated_at", "id"),
}


def search_filter(term: str) -> Q:
    """Build the ``Q`` object for a free-text patient search."""

    cleaned = term.strip()
    query = (
        Q(first_name__icontains=cleaned)
        | Q(last_name__icontains=cleaned)
        | Q(number__icontains=cleaned)
        | Q(email__icontains=cleaned)
    )

    parts = [part for part in cleaned.split() if part]
    if len(parts) > 1:
        for part in parts:
            query |= Q(first_name__icontains=part) | Q(last_name__icontains=part)

    digits = phone_search_digits(cleaned)
    if digits:
        query |= Q(phone_e164__icontains=digits)
    return query


def apply_patient_filters(queryset: QuerySet, params: Any) -> QuerySet:
    """Apply ``search``/``status``/``source``/``location`` filters."""

    term = (params.get("search") or "").strip()
    if term:
        queryset = queryset.filter(search_filter(term))

    status = (params.get("status") or "").strip()
    if status:
        queryset = queryset.filter(status=status)

    source = (params.get("source") or "").strip()
    if source:
        queryset = queryset.filter(source=source)

    location = (params.get("location") or "").strip()
    if location:
        queryset = queryset.filter(primary_location_id=location)

    provider = (params.get("provider") or "").strip()
    if provider:
        queryset = queryset.filter(primary_provider_id=provider)

    # Deleted and merged records are never part of a list.
    return queryset.filter(deleted_at__isnull=True, merged_into__isnull=True)


def apply_patient_ordering(queryset: QuerySet, params: Any) -> QuerySet:
    """Apply ``ordering``; an unknown value is a field-addressed 400."""

    raw = (params.get("ordering") or "").strip()
    if not raw:
        return queryset.order_by(*DEFAULT_ORDERING)
    if raw not in ORDERING_CHOICES:
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={
                "ordering": [f"select one of: {', '.join(sorted(ORDERING_CHOICES))}"]
            },
        )
    return queryset.order_by(*ORDERING_CHOICES[raw])


__all__ = [
    "DEFAULT_ORDERING",
    "ORDERING_CHOICES",
    "apply_patient_filters",
    "apply_patient_ordering",
    "search_filter",
]
