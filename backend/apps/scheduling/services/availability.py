"""Server-computed availability (T110).

The slot engine is the only place that turns rules into bookable time. It is
deliberately deterministic and side-effect free so the public booking API, the
staff UI and the metrics service can all share it.

Time model
----------
``AvailabilityRule`` stores a *local* ``time`` plus a weekday; expansion happens
in the clinic's IANA zone and the resulting instants are UTC. Rule boundaries
are resolved with ``fold=0``:

* a wall-clock time that does not exist (spring-forward gap) is **skipped**;
* a wall-clock time that happens twice (fall-back overlap) resolves to the
  **first** occurrence.

Slots are stepped in local wall-clock time and each candidate is then resolved.
Documented DST results: a ``09:00``-style rule is unaffected; for a rule that
spans the transition (``00:00``-``04:00``, 15-minute slots, 15-minute duration)
the spring-forward day yields **11** slots -- the four candidates that would
start inside the ``02:00``-``02:59`` gap plus the ``01:45`` slot that would end
inside it are skipped -- and the fall-back day yields **16**, because the
repeated ``01:00``-``01:59`` hour is offered once.

Exclusions
----------
A candidate slot is dropped when it overlaps a break/block rule, a partial
schedule exception, an all-day exception (the whole day), an existing active
appointment (expanded by that appointment type's buffers), the lead-time cutoff,
or -- for operatory-bound types -- when no operatory at the location is free.
Every exclusion is counted in ``window["reasons"]``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from datetime import date as date_type
from datetime import time as time_type
from typing import Any
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.clinics.models import Location, Provider
from apps.core.errors import ApiError

from ..models import Appointment, AvailabilityRule, Operatory, ScheduleException
from ..status import ACTIVE_STATUSES, STATUS_HELD

DEFAULT_STEP_MIN = 15
DEFAULT_LEAD_TIME_HOURS = 2.0
MAX_WINDOW_DAYS = 31

#: Exclusion reasons surfaced in the window summary.
REASON_NO_RULE = "no_rule"
REASON_HOLIDAY = "holiday"
REASON_BOOKED = "booked"
REASON_LEAD_TIME = "lead_time"
REASON_OPERATORY_UNAVAILABLE = "operatory_unavailable"


# --------------------------------------------------------------------------- #
# Clinic configuration
# --------------------------------------------------------------------------- #


def clinic_timezone(clinic: Any) -> ZoneInfo:
    """Return the clinic zone, falling back to the process default."""

    name = getattr(clinic, "timezone", "") or ""
    try:
        return ZoneInfo(name)
    except Exception:  # pragma: no cover - defensive; clinics validate tz on write
        return ZoneInfo("UTC")


def booking_policy(clinic: Any) -> dict[str, float]:
    """Read the booking policy from ``clinic.settings_json``.

    ``booking.BookingPolicy`` lands with the public booking task; until then the
    policy is a documented key in the clinic settings blob so scheduling has a
    single place to read ``lead_time_hours`` from.
    """

    settings = getattr(clinic, "settings_json", None) or {}
    policy = settings.get("booking_policy") or {}
    try:
        lead_time_hours = float(policy.get("lead_time_hours", DEFAULT_LEAD_TIME_HOURS))
    except (TypeError, ValueError):
        lead_time_hours = DEFAULT_LEAD_TIME_HOURS
    return {"lead_time_hours": lead_time_hours}


def slot_step_min(clinic: Any) -> int:
    """Slot granularity in minutes (``settings_json.scheduling.slot_step_min``)."""

    settings = getattr(clinic, "settings_json", None) or {}
    scheduling = settings.get("scheduling") or {}
    try:
        step = int(scheduling.get("slot_step_min", DEFAULT_STEP_MIN))
    except (TypeError, ValueError):
        step = DEFAULT_STEP_MIN
    return step if step > 0 else DEFAULT_STEP_MIN


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #


def local_instant(naive: datetime, tz: ZoneInfo) -> datetime | None:
    """Resolve a naive wall-clock datetime to an aware UTC instant.

    Returns ``None`` for a non-existent local time (spring-forward gap). An
    ambiguous local time resolves to its first occurrence (``fold=0``).
    """

    if naive.tzinfo is not None:
        # Defensive: treat an aware value as an instant and read its wall time.
        naive = naive.astimezone(tz).replace(tzinfo=None)
    aware = naive.replace(tzinfo=tz, fold=0)
    utc = aware.astimezone(UTC)
    if utc.astimezone(tz).replace(tzinfo=None) != naive:
        return None
    return utc


def window_bounds(clinic: Any, from_date: date_type, to_date: date_type) -> tuple[datetime, datetime]:
    """Half-open UTC window ``[from_date 00:00 local, to_date+1 00:00 local)``."""

    tz = clinic_timezone(clinic)
    start_local = datetime.combine(from_date, time_type.min)
    end_local = datetime.combine(to_date + timedelta(days=1), time_type.min)
    start = local_instant(start_local, tz) or start_local.replace(tzinfo=tz).astimezone(
        UTC
    )
    end = local_instant(end_local, tz) or end_local.replace(tzinfo=tz).astimezone(
        UTC
    )
    return start, end


def validate_date_range(from_date: date_type, to_date: date_type) -> int:
    """Validate the window and return its inclusive day count."""

    if from_date is None or to_date is None:
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={"from": ["`from` and `to` are required"]},
        )
    if to_date < from_date:
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={"from": ["must not be after `to`"]},
        )
    days = (to_date - from_date).days + 1
    if days > MAX_WINDOW_DAYS:
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={"to": [f"the window may not exceed {MAX_WINDOW_DAYS} days"]},
            details={"max_days": MAX_WINDOW_DAYS, "requested_days": days},
        )
    return days


# --------------------------------------------------------------------------- #
# Rule selection
# --------------------------------------------------------------------------- #


def rule_is_active(rule: AvailabilityRule, day: date_type) -> bool:
    if rule.valid_from is not None and day < rule.valid_from:
        return False
    if rule.valid_to is not None and day > rule.valid_to:
        return False
    return True


def select_rules(
    rules: list[AvailabilityRule],
    *,
    location_id: Any,
    provider_id: Any,
    weekday: int,
    day: date_type,
    kind: str,
) -> list[AvailabilityRule]:
    """Rules for one lane.

    A provider uses its own rules when it has any for the weekday/location;
    otherwise the clinic-wide (``provider IS NULL``) rules apply. A lane without
    a provider only ever sees clinic-wide rules.
    """

    base = [
        rule
        for rule in rules
        if rule.kind == kind
        and rule.location_id == location_id
        and rule.weekday == weekday
        and rule_is_active(rule, day)
    ]
    if provider_id is None:
        return [rule for rule in base if rule.provider_id is None]
    specific = [rule for rule in base if rule.provider_id == provider_id]
    if specific:
        return specific
    return [rule for rule in base if rule.provider_id is None]


def provider_lanes(clinic: Any, provider: Provider | None = None) -> list[Provider | None]:
    """The provider lanes to expand.

    With an explicit provider, one lane. Otherwise every bookable provider; a
    clinic with no bookable providers expands a single clinic-wide lane.
    """

    if provider is not None:
        return [provider]
    bookable = list(
        Provider.all_objects.filter(clinic=clinic, is_bookable=True).order_by("display_name", "id")
    )
    return bookable or [None]


def locations_for(clinic: Any, location: Location | None = None) -> list[Location]:
    if location is not None:
        return [location]
    return list(Location.all_objects.filter(clinic=clinic, is_active=True).order_by("name", "id"))


# --------------------------------------------------------------------------- #
# Interval expansion
# --------------------------------------------------------------------------- #


def _local_dt(day: date_type, value: time_type) -> datetime:
    return datetime.combine(day, value)


def _subtract(
    segments: list[tuple[datetime, datetime]],
    cut_start: datetime,
    cut_end: datetime,
) -> tuple[list[tuple[datetime, datetime]], int]:
    """Remove ``[cut_start, cut_end)`` from ``segments``; return minutes removed."""

    if cut_end <= cut_start:
        return segments, 0
    out: list[tuple[datetime, datetime]] = []
    removed = 0
    for start, end in segments:
        if cut_end <= start or cut_start >= end:
            out.append((start, end))
            continue
        removed += int((min(cut_end, end) - max(cut_start, start)).total_seconds() // 60)
        if cut_start > start:
            out.append((start, cut_start))
        if cut_end < end:
            out.append((cut_end, end))
    return out, removed


def day_intervals(
    *,
    day: date_type,
    tz: ZoneInfo,
    working_rules: list[AvailabilityRule],
    break_rules: list[AvailabilityRule],
    exceptions: list[ScheduleException],
) -> tuple[list[tuple[datetime, datetime, datetime, datetime]], int, int]:
    """Expand one lane/day into UTC intervals after breaks and exceptions.

    Returns ``(intervals, break_minutes, exception_minutes)`` where each interval
    is ``(start_utc, end_utc, start_local, end_local)``. An all-day exception
    closes the day entirely.
    """

    if any(exc.all_day for exc in exceptions):
        return [], 0, 0
    partials = [
        exc for exc in exceptions if not exc.all_day and exc.start_time and exc.end_time
    ]

    intervals: list[tuple[datetime, datetime, datetime, datetime]] = []
    break_minutes = 0
    exception_minutes = 0
    for rule in working_rules:
        start_local = _local_dt(day, rule.start_time)
        end_local = _local_dt(day, rule.end_time)
        if end_local <= start_local:
            continue
        segments = [(start_local, end_local)]
        for cut in break_rules:
            if not cut.start_time or not cut.end_time:
                continue
            segments, removed = _subtract(
                segments, _local_dt(day, cut.start_time), _local_dt(day, cut.end_time)
            )
            break_minutes += removed
        for exc in partials:
            segments, removed = _subtract(
                segments, _local_dt(day, exc.start_time), _local_dt(day, exc.end_time)
            )
            exception_minutes += removed
        for seg_start, seg_end in segments:
            start_utc = local_instant(seg_start, tz)
            end_utc = local_instant(seg_end, tz)
            if start_utc is None or end_utc is None or end_utc <= start_utc:
                continue
            intervals.append((start_utc, end_utc, seg_start, seg_end))
    return intervals, break_minutes, exception_minutes


def _candidate_slots(
    *,
    start_local: datetime,
    end_local: datetime,
    duration_min: int,
    step_min: int,
    tz: ZoneInfo,
) -> Iterator[tuple[datetime, datetime, datetime, datetime]]:
    """Step local wall-clock candidates and resolve each to UTC."""

    duration = timedelta(minutes=duration_min)
    step = timedelta(minutes=step_min)
    cursor = start_local
    while cursor + duration <= end_local:
        slot_end_local = cursor + duration
        start_utc = local_instant(cursor, tz)
        end_utc = local_instant(slot_end_local, tz)
        if start_utc is not None and end_utc is not None and end_utc > start_utc:
            yield cursor, slot_end_local, start_utc, end_utc
        cursor += step


# --------------------------------------------------------------------------- #
# Conflict lookups
# --------------------------------------------------------------------------- #


class Occupancy:
    """One existing active appointment expanded by its type's buffers."""

    __slots__ = ("provider_id", "operatory_id", "start", "end")

    def __init__(self, provider_id: Any, operatory_id: Any, start: datetime, end: datetime):
        self.provider_id = provider_id
        self.operatory_id = operatory_id
        self.start = start
        self.end = end

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.start < end and start < self.end


def _occupancies(
    clinic: Any,
    window_start: datetime,
    window_end: datetime,
    now: datetime | None = None,
) -> list[Occupancy]:
    """The appointments that occupy the window.

    A **held** appointment only occupies its slot while the hold is alive: a
    hold whose ``hold_expires_at`` has passed no longer blocks the slot (the
    hold sweeper releases it, and an abandoned hold must not remove the slot
    from the pool permanently). Every other active status always occupies the
    slot.
    """

    moment = now or timezone.now()
    rows = (
        Appointment.all_objects.filter(
            clinic=clinic,
            status__in=sorted(ACTIVE_STATUSES),
            start_at__lt=window_end,
            end_at__gt=window_start,
        )
        # An elapsed hold is not an occupancy any more.
        .exclude(
            status=STATUS_HELD,
            hold_expires_at__isnull=False,
            hold_expires_at__lte=moment,
        )
        .select_related("appointment_type")
        .only(
            "provider_id",
            "operatory_id",
            "start_at",
            "end_at",
            "appointment_type__buffer_before_min",
            "appointment_type__buffer_after_min",
        )
    )
    result = []
    for row in rows:
        before = timedelta(minutes=row.appointment_type.buffer_before_min)
        after = timedelta(minutes=row.appointment_type.buffer_after_min)
        result.append(
            Occupancy(row.provider_id, row.operatory_id, row.start_at - before, row.end_at + after)
        )
    return result


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def compute_slots(
    clinic: Any,
    appointment_type: Any,
    from_date: date_type,
    to_date: date_type,
    *,
    provider: Provider | None = None,
    location: Location | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return ``{"slots": [...], "window": {...}}`` for the requested range.

    Raises ``ApiError`` (400) when the range is invalid or wider than
    :data:`MAX_WINDOW_DAYS`.
    """

    days = validate_date_range(from_date, to_date)
    tz = clinic_timezone(clinic)
    step_min = slot_step_min(clinic)
    lead_time_hours = booking_policy(clinic)["lead_time_hours"]
    now = now or timezone.now()
    lead_cutoff = now + timedelta(hours=lead_time_hours)

    duration_min = appointment_type.duration_min
    buffer_before = timedelta(minutes=appointment_type.buffer_before_min)
    buffer_after = timedelta(minutes=appointment_type.buffer_after_min)
    requires_operatory = bool(appointment_type.requires_operatory)

    locations = locations_for(clinic, location)
    lanes = provider_lanes(clinic, provider)
    rules = list(AvailabilityRule.all_objects.filter(clinic=clinic))
    exceptions = list(
        ScheduleException.all_objects.filter(
            clinic=clinic, date__gte=from_date, date__lte=to_date
        )
    )
    operatories = list(
        Operatory.all_objects.filter(clinic=clinic, is_active=True).order_by("name", "id")
    )

    window_start, window_end = window_bounds(clinic, from_date, to_date)
    occupancies = _occupancies(clinic, window_start, window_end, now=now)

    reasons: Counter[str] = Counter()
    slots: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()

    day = from_date
    while day <= to_date:
        weekday = day.weekday()
        for loc in locations:
            for lane in lanes:
                provider_id = lane.pk if lane is not None else None
                working = select_rules(
                    rules,
                    location_id=loc.pk,
                    provider_id=provider_id,
                    weekday=weekday,
                    day=day,
                    kind=AvailabilityRule.Kind.WORKING,
                )
                if not working:
                    reasons[REASON_NO_RULE] += 1
                    continue

                lane_exceptions = [
                    exc
                    for exc in exceptions
                    if exc.date == day
                    and exc.location_id == loc.pk
                    and (exc.provider_id is None or exc.provider_id == provider_id)
                ]
                if any(exc.all_day for exc in lane_exceptions):
                    reasons[REASON_HOLIDAY] += 1
                    continue

                break_rules = select_rules(
                    rules,
                    location_id=loc.pk,
                    provider_id=provider_id,
                    weekday=weekday,
                    day=day,
                    kind=AvailabilityRule.Kind.BREAK,
                ) + select_rules(
                    rules,
                    location_id=loc.pk,
                    provider_id=provider_id,
                    weekday=weekday,
                    day=day,
                    kind=AvailabilityRule.Kind.BLOCK,
                )

                intervals, break_minutes, exception_minutes = day_intervals(
                    day=day,
                    tz=tz,
                    working_rules=working,
                    break_rules=break_rules,
                    exceptions=lane_exceptions,
                )
                if break_minutes:
                    reasons["break_minutes"] += break_minutes
                if exception_minutes:
                    reasons["exception_minutes"] += exception_minutes

                for _start_utc, _end_utc, start_local, end_local in intervals:
                    for _, _, slot_start, slot_end in _candidate_slots(
                        start_local=start_local,
                        end_local=end_local,
                        duration_min=duration_min,
                        step_min=step_min,
                        tz=tz,
                    ):
                        occ_start = slot_start - buffer_before
                        occ_end = slot_end + buffer_after

                        if slot_start < lead_cutoff:
                            reasons[REASON_LEAD_TIME] += 1
                            continue

                        if any(
                            occ.provider_id == provider_id and occ.overlaps(occ_start, occ_end)
                            for occ in occupancies
                        ):
                            reasons[REASON_BOOKED] += 1
                            continue

                        operatory = None
                        if requires_operatory:
                            for candidate in operatories:
                                if candidate.location_id != loc.pk:
                                    continue
                                if not any(
                                    occ.operatory_id == candidate.pk
                                    and occ.overlaps(occ_start, occ_end)
                                    for occ in occupancies
                                ):
                                    operatory = candidate
                                    break
                            if operatory is None:
                                reasons[REASON_OPERATORY_UNAVAILABLE] += 1
                                continue

                        key = (slot_start, provider_id, operatory.pk if operatory else None)
                        if key in seen:
                            continue
                        seen.add(key)
                        slots.append(
                            {
                                "start": slot_start,
                                "end": slot_end,
                                "provider": lane,
                                "operatory": operatory,
                                "location": loc,
                                "reason": "available",
                            }
                        )
        day += timedelta(days=1)

    slots.sort(key=lambda item: (item["start"], str(item["provider"] or "")))
    reason_summary = ", ".join(f"{key}={value}" for key, value in sorted(reasons.items()))
    window = {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "timezone": str(tz),
        "step_min": step_min,
        "lead_time_hours": lead_time_hours,
        "max_days": MAX_WINDOW_DAYS,
        "days": days,
        "start_at": window_start,
        "end_at": window_end,
        "slot_count": len(slots),
        "reasons": dict(sorted(reasons.items())),
        "reason": reason_summary or "no exclusions",
    }
    return {"slots": slots, "window": window}


def available_minutes(
    clinic: Any,
    from_date: date_type,
    to_date: date_type,
    *,
    location: Location | None = None,
    provider: Provider | None = None,
) -> int:
    """Expanded rule minutes for the metrics denominator.

    Sums the real (UTC elapsed) duration of every working interval per
    ``(location, provider lane)`` after breaks/blocks and schedule exceptions.
    A lane is counted once, so a clinic with N bookable providers has N times the
    wall-clock hours. DST: the spring-forward day is genuinely one hour shorter
    and the fall-back day one hour longer.
    """

    tz = clinic_timezone(clinic)
    locations = locations_for(clinic, location)
    lanes = provider_lanes(clinic, provider)
    rules = list(AvailabilityRule.all_objects.filter(clinic=clinic))
    exceptions = list(
        ScheduleException.all_objects.filter(
            clinic=clinic, date__gte=from_date, date__lte=to_date
        )
    )

    total = 0.0
    day = from_date
    while day <= to_date:
        weekday = day.weekday()
        for loc in locations:
            for lane in lanes:
                provider_id = lane.pk if lane is not None else None
                working = select_rules(
                    rules,
                    location_id=loc.pk,
                    provider_id=provider_id,
                    weekday=weekday,
                    day=day,
                    kind=AvailabilityRule.Kind.WORKING,
                )
                if not working:
                    continue
                lane_exceptions = [
                    exc
                    for exc in exceptions
                    if exc.date == day
                    and exc.location_id == loc.pk
                    and (exc.provider_id is None or exc.provider_id == provider_id)
                ]
                if any(exc.all_day for exc in lane_exceptions):
                    continue
                break_rules = select_rules(
                    rules,
                    location_id=loc.pk,
                    provider_id=provider_id,
                    weekday=weekday,
                    day=day,
                    kind=AvailabilityRule.Kind.BREAK,
                ) + select_rules(
                    rules,
                    location_id=loc.pk,
                    provider_id=provider_id,
                    weekday=weekday,
                    day=day,
                    kind=AvailabilityRule.Kind.BLOCK,
                )
                intervals, _, _ = day_intervals(
                    day=day,
                    tz=tz,
                    working_rules=working,
                    break_rules=break_rules,
                    exceptions=lane_exceptions,
                )
                for start_utc, end_utc, _, _ in intervals:
                    total += (end_utc - start_utc).total_seconds() / 60.0
        day += timedelta(days=1)
    return int(round(total))


__all__ = [
    "DEFAULT_LEAD_TIME_HOURS",
    "DEFAULT_STEP_MIN",
    "MAX_WINDOW_DAYS",
    "REASON_BOOKED",
    "REASON_HOLIDAY",
    "REASON_LEAD_TIME",
    "REASON_NO_RULE",
    "REASON_OPERATORY_UNAVAILABLE",
    "available_minutes",
    "booking_policy",
    "clinic_timezone",
    "compute_slots",
    "day_intervals",
    "local_instant",
    "locations_for",
    "provider_lanes",
    "rule_is_active",
    "select_rules",
    "slot_step_min",
    "validate_date_range",
    "window_bounds",
]
