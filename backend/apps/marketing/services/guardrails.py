"""Marketing guardrails: the segment field allowlist and daily caps (T132).

Two hard limits shape every segment and campaign:

1. **Only non-clinical fields may be selected.** The allowlist is
   ``status``, ``last_visit_days``, ``recall_due``, ``plan_status``,
   ``location`` and ``source``. A predicate that names a clinical concept
   (diagnosis, note, allergy, tooth, procedure, ...) is rejected with a
   :class:`PredicateError`, so a segment can never become a clinical query.
2. **A clinic-local daily cap bounds a campaign.** ``sends_today`` counts the
   recipients already sent on the campaign inside the clinic's current local
   day; the activator pauses the campaign once the cap is reached instead of
   exceeding it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

#: The only fields a segment predicate may reference.
ALLOWED_FIELDS: tuple[str, ...] = (
    "status",
    "last_visit_days",
    "recall_due",
    "plan_status",
    "location",
    "source",
)

#: Comparison operators the predicate DSL understands.
ALLOWED_OPS: tuple[str, ...] = ("eq", "gte", "lte", "in", "between")

#: Logical composition keys. Exactly one must be present.
LOGICAL_KEYS: tuple[str, ...] = ("all", "any")

#: Substrings that mark clinical data. A field whose name contains one is
#: rejected even before the allowlist check so the error names the real reason.
CLINICAL_TOKENS: tuple[str, ...] = (
    "diagnos",
    "clinical",
    "note",
    "medical",
    "allerg",
    "condition",
    "tooth",
    "chart",
    "procedure",
    "prescription",
    "treatment",
    "medication",
    "encounter",
    "symptom",
)


class PredicateError(ValueError):
    """A segment predicate that is malformed or names a forbidden field."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def check_field(field: Any) -> str:
    """Return the normalised field name or raise :class:`PredicateError`."""

    name = str(field or "").strip()
    if name in ALLOWED_FIELDS:
        return name
    lowered = name.lower()
    if any(token in lowered for token in CLINICAL_TOKENS):
        raise PredicateError(
            f"clinical field '{name}' is not allowed in a marketing segment"
        )
    raise PredicateError(f"unsupported segment field '{name}'")


def _clinic_timezone(clinic: Any) -> ZoneInfo:
    name = str(getattr(clinic, "timezone", "") or settings.TIME_ZONE)
    try:
        return ZoneInfo(name)
    except Exception:  # pragma: no cover - invalid configured zone
        return ZoneInfo(settings.TIME_ZONE)


def clinic_day_bounds(clinic: Any, moment: datetime) -> tuple[datetime, datetime]:
    """Return the ``[start, end)`` UTC bounds of the clinic-local day of ``moment``."""

    tz = _clinic_timezone(clinic)
    local = moment.astimezone(tz)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def sends_today(campaign: Any, moment: datetime | None = None) -> int:
    """Count recipients already sent on ``campaign`` in the current local day."""

    from ..models import CampaignRecipient

    when = moment or timezone.now()
    start, end = clinic_day_bounds(campaign.clinic, when)
    return CampaignRecipient.all_objects.filter(
        campaign_id=campaign.pk,
        status=CampaignRecipient.Status.SENT,
        message__sent_at__gte=start,
        message__sent_at__lt=end,
    ).count()


__all__ = [
    "ALLOWED_FIELDS",
    "ALLOWED_OPS",
    "CLINICAL_TOKENS",
    "LOGICAL_KEYS",
    "PredicateError",
    "check_field",
    "clinic_day_bounds",
    "sends_today",
]
