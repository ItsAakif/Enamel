"""Send policy: quiet hours and frequency caps (T114).

Both knobs default from settings and may be overridden per clinic in
``clinic.settings_json["messaging"]``. All wall-clock reasoning happens in the
clinic's IANA timezone; the returned instants are UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from apps.scheduling.services.availability import clinic_timezone

from ..models import Message

DEFAULT_QUIET_START = 21
DEFAULT_QUIET_END = 8
DEFAULT_FREQUENCY_CAP = 4
DEFAULT_FREQUENCY_PERIOD_DAYS = 7
DEFAULT_MAX_ATTEMPTS = 3
RETRY_BASE_SECONDS = 30.0
RETRY_FACTOR = 2.0
#: Statuses that consume the frequency budget (a skip/failure does not).
COUNTED_STATUSES = (
    Message.Status.QUEUED,
    Message.Status.SENT,
    Message.Status.DELIVERED,
)


def _clinic_messaging(clinic: Any) -> dict[str, Any]:
    settings_json = getattr(clinic, "settings_json", None) or {}
    messaging = settings_json.get("messaging") or {}
    return messaging if isinstance(messaging, dict) else {}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def quiet_hours(clinic: Any) -> tuple[int, int]:
    """Return the ``(start_hour, end_hour)`` quiet window (clinic-local)."""

    override = _clinic_messaging(clinic)
    start = _int(
        override.get("quiet_hours_start", getattr(settings, "MESSAGING_QUIET_HOURS_START", DEFAULT_QUIET_START)),
        DEFAULT_QUIET_START,
    )
    end = _int(
        override.get("quiet_hours_end", getattr(settings, "MESSAGING_QUIET_HOURS_END", DEFAULT_QUIET_END)),
        DEFAULT_QUIET_END,
    )
    return max(0, min(23, start)), max(0, min(23, end))


def in_quiet_hours(clinic: Any, moment: datetime) -> bool:
    """True when ``moment`` falls inside the clinic-local quiet window."""

    start, end = quiet_hours(clinic)
    if start == end:
        return False
    tz: ZoneInfo = clinic_timezone(clinic)
    hour = moment.astimezone(tz).hour
    if start < end:
        return start <= hour < end
    # Wraps midnight, e.g. 21:00 -> 08:00.
    return hour >= start or hour < end


def next_window_start(clinic: Any, moment: datetime) -> datetime:
    """Return the UTC instant the next allowed window opens.

    When ``moment`` is not inside a quiet window it is returned unchanged.
    """

    if not in_quiet_hours(clinic, moment):
        return moment
    tz: ZoneInfo = clinic_timezone(clinic)
    _, end = quiet_hours(clinic)
    local = moment.astimezone(tz)
    candidate = local.replace(hour=end, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(UTC)


def frequency_cap(clinic: Any) -> tuple[int, int]:
    """Return ``(cap, period_days)`` for the clinic (cap 0 disables the cap)."""

    override = _clinic_messaging(clinic)
    cap = _int(
        override.get("frequency_cap", getattr(settings, "MESSAGING_FREQUENCY_CAP", DEFAULT_FREQUENCY_CAP)),
        DEFAULT_FREQUENCY_CAP,
    )
    period = _int(
        override.get(
            "frequency_period_days",
            getattr(settings, "MESSAGING_FREQUENCY_PERIOD_DAYS", DEFAULT_FREQUENCY_PERIOD_DAYS),
        ),
        DEFAULT_FREQUENCY_PERIOD_DAYS,
    )
    return max(0, cap), max(1, period)


def recent_send_count(patient: Any, channel: str, *, now: datetime | None = None) -> int:
    """Count messages that consume the budget for ``patient``/``channel``."""

    clinic = getattr(patient, "clinic", None)
    _, period_days = frequency_cap(clinic) if clinic is not None else (DEFAULT_FREQUENCY_CAP, DEFAULT_FREQUENCY_PERIOD_DAYS)
    moment = now or timezone.now()
    since = moment - timedelta(days=period_days)
    return Message.all_objects.filter(
        clinic_id=getattr(patient, "clinic_id", None),
        patient_id=getattr(patient, "pk", None),
        channel=channel,
        status__in=COUNTED_STATUSES,
        created_at__gte=since,
    ).count()


def max_attempts() -> int:
    return max(1, _int(getattr(settings, "MESSAGING_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS), DEFAULT_MAX_ATTEMPTS))


def retry_delay(attempts: int) -> timedelta:
    """Exponential backoff for adapter retries (``base * factor**(n-1)``)."""

    exponent = max(0, int(attempts) - 1)
    seconds = RETRY_BASE_SECONDS * (RETRY_FACTOR**exponent)
    return timedelta(seconds=seconds)


__all__ = [
    "COUNTED_STATUSES",
    "DEFAULT_FREQUENCY_CAP",
    "DEFAULT_FREQUENCY_PERIOD_DAYS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_QUIET_END",
    "DEFAULT_QUIET_START",
    "frequency_cap",
    "in_quiet_hours",
    "max_attempts",
    "next_window_start",
    "quiet_hours",
    "recent_send_count",
    "retry_delay",
]
