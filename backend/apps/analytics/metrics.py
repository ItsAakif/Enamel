"""Analytics metric definitions as data, plus the date-range contract (T133).

Definitions live here, never in a view. Each metric is a :class:`MetricSpec`:
the key, a label, a unit, and -- critically -- the *definition* of its numerator
and denominator as text. ``registry.payload`` turns a spec plus three numbers
into the API shape::

    {"key", "label", "unit", "definition",
     "numerator", "denominator",          # the textual definitions
     "numerator_value", "denominator_value", "value"}

Date contract
-------------
A range is given as two clinic-local **dates**. It resolves to the half-open
UTC interval ``[from 00:00 local, to+1 00:00 local)`` in the clinic timezone:

* the **start is inclusive**: an appointment at exactly ``from 00:00`` counts;
* the **end is exclusive**: an appointment at exactly ``to+1 00:00`` does not.

Both bounds are resolved by constructing the local midnight and converting it,
so a DST transition inside the range is reflected in real elapsed time and the
day count is unaffected. This is the same contract as
``scheduling.services.availability.window_bounds`` and is asserted by
``tests/test_date_filters.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import date as date_type
from datetime import time as time_type
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class MetricSpec:
    """One metric: its identity, its unit and the text of its fraction."""

    key: str
    label: str
    unit: str
    definition: str
    numerator: str
    denominator: str

    def as_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "definition": self.definition,
            "numerator": self.numerator,
            "denominator": self.denominator,
        }


#: Every metric the API can return. ``unit`` is ``count``, ``ratio``,
#: ``minor_units`` (integer minor currency units) or ``minutes``.
SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        key="appointments.total",
        label="Appointments",
        unit="count",
        definition="Appointments whose start_at falls inside the clinic-local window.",
        numerator="count of appointments in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="appointments.completed",
        label="Completed",
        unit="count",
        definition="Appointments in the window whose current status is completed.",
        numerator="count of completed appointments in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="appointments.cancelled",
        label="Cancelled",
        unit="count",
        definition="Appointments in the window whose current status is cancelled.",
        numerator="count of cancelled appointments in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="appointments.no_show",
        label="No-shows",
        unit="count",
        definition="Appointments in the window whose current status is no_show.",
        numerator="count of no-show appointments in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="appointments.completion_rate",
        label="Completion rate",
        unit="ratio",
        definition="Completed appointments divided by all appointments in the window.",
        numerator="appointments.completed",
        denominator="appointments.total",
    ),
    MetricSpec(
        key="appointments.cancellation_rate",
        label="Cancellation rate",
        unit="ratio",
        definition="Cancelled appointments divided by all appointments in the window.",
        numerator="appointments.cancelled",
        denominator="appointments.total",
    ),
    MetricSpec(
        key="appointments.no_show_rate",
        label="No-show rate",
        unit="ratio",
        definition="No-show appointments divided by all appointments in the window.",
        numerator="appointments.no_show",
        denominator="appointments.total",
    ),
    MetricSpec(
        key="appointments.utilisation",
        label="Utilisation",
        unit="ratio",
        definition=(
            "Booked time divided by expanded bookable time. Booked time is the "
            "sum of end_at - start_at for consuming statuses (scheduled, "
            "confirmed, checked_in, in_progress, completed); the denominator is "
            "the expanded working minutes per (location, provider lane) minus "
            "breaks/blocks and schedule exceptions, in real UTC minutes. A "
            "clinic with N bookable providers has N times the wall-clock hours."
        ),
        numerator="appointments.booked_minutes",
        denominator="scheduling.availability.available_minutes",
    ),
    MetricSpec(
        key="appointments.booked_minutes",
        label="Booked minutes",
        unit="minutes",
        definition="Real elapsed minutes booked by consuming appointments.",
        numerator="sum of (end_at - start_at) for consuming statuses",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="appointments.available_minutes",
        label="Available minutes",
        unit="minutes",
        definition=(
            "Expanded working minutes per (location, provider lane) after "
            "breaks/blocks and schedule exceptions, in real UTC minutes."
        ),
        numerator="expanded working minutes",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="patients.new",
        label="New patients",
        unit="count",
        definition=(
            "Distinct patients whose first ever appointment in the clinic starts "
            "inside the window."
        ),
        numerator="count of patients whose earliest appointment is in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="patients.returning",
        label="Returning patients",
        unit="count",
        definition=(
            "Distinct patients with an appointment in the window whose earliest "
            "ever appointment is before the window."
        ),
        numerator="count of patients with an earlier appointment",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="revenue.invoiced_minor",
        label="Invoiced",
        unit="minor_units",
        definition="Sum of invoice totals issued inside the window.",
        numerator="sum of invoice.total_minor for invoices issued in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="revenue.collected_minor",
        label="Collected",
        unit="minor_units",
        definition="Sum of succeeded payments captured inside the window.",
        numerator="sum of payment.amount_minor for succeeded payments in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="revenue.outstanding_minor",
        label="Outstanding",
        unit="minor_units",
        definition=(
            "Sum of invoice balances for open invoices (issued or partially "
            "paid) issued inside the window."
        ),
        numerator="sum of invoice.balance_minor for open invoices issued in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="revenue.collection_rate",
        label="Collection rate",
        unit="ratio",
        definition="Collected revenue divided by invoiced revenue in the window.",
        numerator="revenue.collected_minor",
        denominator="revenue.invoiced_minor",
    ),
    MetricSpec(
        key="recall.due",
        label="Recall due",
        unit="count",
        definition="Recall rows that are due (status due) with due_at in the window.",
        numerator="count of due recall rows in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="recall.completed",
        label="Recall completed",
        unit="count",
        definition=(
            "Recall rows completed (status completed) with last_completed_at in "
            "the window."
        ),
        numerator="count of completed recall rows in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="recall.completion_rate",
        label="Recall completion rate",
        unit="ratio",
        definition="Completed recalls divided by due recalls in the window.",
        numerator="recall.completed",
        denominator="recall.due",
    ),
    MetricSpec(
        key="reminders.sent",
        label="Reminders sent",
        unit="count",
        definition="Messages with a sent_at inside the window.",
        numerator="count of messages sent in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="reminders.delivered",
        label="Reminders delivered",
        unit="count",
        definition="Messages with a delivered_at inside the window.",
        numerator="count of messages delivered in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="reminders.failed",
        label="Reminders failed",
        unit="count",
        definition=(
            "Messages created inside the window whose status is failed or dead."
        ),
        numerator="count of failed or dead messages created in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="reminders.delivery_rate",
        label="Delivery rate",
        unit="ratio",
        definition="Delivered reminders divided by sent reminders in the window.",
        numerator="reminders.delivered",
        denominator="reminders.sent",
    ),
    MetricSpec(
        key="waitlist.offers",
        label="Waitlist offers",
        unit="count",
        definition="Waitlist offers created inside the window.",
        numerator="count of offers created in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="waitlist.claims",
        label="Waitlist claims",
        unit="count",
        definition="Waitlist offers created in the window whose status is claimed.",
        numerator="count of claimed offers in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="waitlist.expired",
        label="Waitlist expiries",
        unit="count",
        definition="Waitlist offers created in the window whose status is expired.",
        numerator="count of expired offers in the window",
        denominator="1 (count metric)",
    ),
    MetricSpec(
        key="waitlist.claim_rate",
        label="Waitlist claim rate",
        unit="ratio",
        definition="Claimed offers divided by offers created in the window.",
        numerator="waitlist.claims",
        denominator="waitlist.offers",
    ),
)

SPEC_BY_KEY: dict[str, MetricSpec] = {spec.key: spec for spec in SPECS}


def clinic_timezone(clinic: Any) -> ZoneInfo:
    """Return the clinic zone, falling back to UTC."""

    name = getattr(clinic, "timezone", "") or ""
    try:
        return ZoneInfo(name)
    except Exception:  # pragma: no cover - clinics validate tz on write
        return ZoneInfo("UTC")


def resolve_range(
    clinic: Any,
    from_date: date_type,
    to_date: date_type,
) -> tuple[datetime, datetime]:
    """Resolve a clinic-local date range to ``[start, end)`` in UTC.

    ``from_date`` is inclusive, ``to_date`` inclusive as a date, so the returned
    interval ends at ``to_date + 1 day`` local midnight and excludes it.
    """

    tz = clinic_timezone(clinic)
    start_local = datetime.combine(from_date, time_type.min, tzinfo=tz)
    end_local = datetime.combine(to_date + timedelta(days=1), time_type.min, tzinfo=tz)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def range_payload(
    clinic: Any,
    from_date: date_type,
    to_date: date_type,
    *,
    location: Any = None,
) -> dict[str, Any]:
    """The window block returned alongside every metric list."""

    start, end = resolve_range(clinic, from_date, to_date)
    return {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "timezone": str(clinic_timezone(clinic)),
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "inclusive_start": True,
        "exclusive_end": True,
        "location_id": str(getattr(location, "id", location)) if location is not None else None,
    }


def day_series_bounds(
    clinic: Any,
    from_date: date_type,
    to_date: date_type,
) -> list[tuple[date_type, datetime, datetime]]:
    """Yield one ``(local_date, start, end)`` triple per day in the range."""

    tz = clinic_timezone(clinic)
    days: list[tuple[date_type, datetime, datetime]] = []
    day = from_date
    while day <= to_date:
        start = datetime.combine(day, time_type.min, tzinfo=tz).astimezone(UTC)
        end = datetime.combine(day + timedelta(days=1), time_type.min, tzinfo=tz).astimezone(
            UTC
        )
        days.append((day, start, end))
        day += timedelta(days=1)
    return days


__all__ = [
    "SPECS",
    "SPEC_BY_KEY",
    "MetricSpec",
    "clinic_timezone",
    "day_series_bounds",
    "range_payload",
    "resolve_range",
]
