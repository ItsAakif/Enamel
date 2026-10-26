"""Clinic monthly AI budget guard (T123).

The guard runs *before* any provider call. When a clinic has spent its monthly
allowance the invocation is refused with the stable code ``budget_exceeded`` and
the provider is never contacted -- no spend can accrue past the cap.

Configuration, in priority order:

1. ``clinic.settings_json["ai"]["monthly_budget_minor"]`` (per-clinic override);
2. the ``AI_MONTHLY_BUDGET_MINOR`` setting;
3. ``0``, which disables the guard (unlimited).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone


class BudgetExceeded(RuntimeError):
    """Raised when a clinic has spent its monthly AI allowance."""

    code = "budget_exceeded"

    def __init__(self, *, spent_minor: int, budget_minor: int) -> None:
        super().__init__("The clinic AI budget for this month has been reached.")
        self.spent_minor = int(spent_minor)
        self.budget_minor = int(budget_minor)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def monthly_budget_minor(clinic: Any) -> int:
    """Return the clinic's monthly allowance in minor units (0 = unlimited)."""

    if clinic is not None:
        settings_json = getattr(clinic, "settings_json", None) or {}
        ai_settings = settings_json.get("ai") if isinstance(settings_json, dict) else None
        if isinstance(ai_settings, dict) and "monthly_budget_minor" in ai_settings:
            return max(0, _int(ai_settings.get("monthly_budget_minor"), 0))
    return max(0, _int(getattr(settings, "AI_MONTHLY_BUDGET_MINOR", 0), 0))


def month_start(moment: datetime | None = None) -> datetime:
    current = moment or timezone.now()
    return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def spent_minor(clinic: Any, *, now: datetime | None = None) -> int:
    """Sum this month's recorded AI spend for ``clinic``."""

    if clinic is None:
        return 0
    from ..models import AIInvocation

    clinic_id = getattr(clinic, "id", clinic)
    aggregate = AIInvocation.objects.filter(
        clinic_id=clinic_id,
        created_at__gte=month_start(now),
    ).aggregate(total=Sum("cost_minor"))
    return int(aggregate.get("total") or 0)


def check_budget(clinic: Any, *, now: datetime | None = None) -> int:
    """Return the remaining allowance, or raise :class:`BudgetExceeded`.

    A budget of ``0`` (the default) disables the guard.
    """

    budget = monthly_budget_minor(clinic)
    if budget <= 0 or clinic is None:
        return 0
    spent = spent_minor(clinic, now=now)
    if spent >= budget:
        raise BudgetExceeded(spent_minor=spent, budget_minor=budget)
    return budget - spent


__all__ = [
    "BudgetExceeded",
    "check_budget",
    "month_start",
    "monthly_budget_minor",
    "spent_minor",
]
