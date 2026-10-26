"""Date-range semantics: DST, month boundary, inclusive/exclusive (T133).

Three families of assertion, each on a real clinic timezone:

1. **DST day** -- ``America/New_York`` on 2026-03-08 (spring forward) is 23 real
   hours; the window must reflect that and still include the local midnight.
2. **Month boundary** -- a June window excludes the last instant of May and the
   first instant of July.
3. **Inclusive start / exclusive end** -- a single-day window includes exactly
   that local day.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.analytics.metrics import resolve_range
from apps.analytics.services import appointments
from apps.scheduling.models import Appointment

pytestmark = pytest.mark.django_db

KOLKATA = ZoneInfo("Asia/Kolkata")
NEW_YORK = ZoneInfo("America/New_York")


def local(zone, year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=zone).astimezone(UTC)


def test_dst_day_window_is_23_real_hours_and_half_open(
    staff_factory, location_factory, provider_factory, appointment_type_factory, patient_factory
):
    from apps.clinics.models import Clinic

    owner = staff_factory(role="owner", slug="dst-clinic")
    # ``clinic_factory`` pins Asia/Kolkata, so the DST clinic is retimed here.
    Clinic.objects.filter(pk=owner.clinic.pk).update(timezone="America/New_York")
    owner.clinic.refresh_from_db()
    clinic = owner.clinic
    location = location_factory(clinic)
    provider = provider_factory(clinic, membership=owner.membership)
    appointment_type = appointment_type_factory(clinic)
    patient = patient_factory(clinic)

    day = date(2026, 3, 8)
    start, end = resolve_range(clinic, day, day)

    # 2026-03-08 00:00 EST is 05:00 UTC; 2026-03-09 00:00 EDT is 04:00 UTC.
    assert start == datetime(2026, 3, 8, 5, 0, tzinfo=UTC)
    assert end == datetime(2026, 3, 9, 4, 0, tzinfo=UTC)
    assert end - start == timedelta(hours=23)

    # The first instant of the local day is inside; the last instant of the
    # previous day and the first instant of the next day are outside.
    Appointment.all_objects.create(
        clinic=clinic,
        location=location,
        provider=provider,
        appointment_type=appointment_type,
        patient=patient,
        start_at=start,
        end_at=start + timedelta(minutes=30),
        status="completed",
    )
    before = Appointment.all_objects.create(
        clinic=clinic,
        location=location,
        provider=provider,
        appointment_type=appointment_type,
        patient=patient,
        start_at=start - timedelta(minutes=1),
        end_at=start + timedelta(minutes=29),
        status="completed",
    )
    after = Appointment.all_objects.create(
        clinic=clinic,
        location=location,
        provider=provider,
        appointment_type=appointment_type,
        patient=patient,
        start_at=end,
        end_at=end + timedelta(minutes=30),
        status="completed",
    )

    total = appointments.total(clinic, day, day)
    assert total["value"] == 1
    assert before.start_at < start < end <= after.start_at


def test_month_boundary_excludes_the_adjacent_months(
    staff_factory, location_factory, provider_factory, appointment_type_factory, patient_factory
):
    owner = staff_factory(role="owner", slug="boundary-clinic")
    clinic = owner.clinic
    location = location_factory(clinic)
    provider = provider_factory(clinic, membership=owner.membership)
    appointment_type = appointment_type_factory(clinic)
    patient = patient_factory(clinic)

    def _create(start_at):
        return Appointment.all_objects.create(
            clinic=clinic,
            location=location,
            provider=provider,
            appointment_type=appointment_type,
            patient=patient,
            start_at=start_at,
            end_at=start_at + timedelta(minutes=30),
            status="completed",
        )

    june_start = local(KOLKATA, 2026, 6, 1)
    june_end = local(KOLKATA, 2026, 7, 1)
    _create(june_start - timedelta(minutes=1))  # 2026-05-31 23:59 local
    _create(june_start)  # 2026-06-01 00:00 local (inclusive)
    _create(june_end - timedelta(minutes=1))  # 2026-06-30 23:59 local
    _create(june_end)  # 2026-07-01 00:00 local (excluded)

    total = appointments.total(clinic, date(2026, 6, 1), date(2026, 6, 30))
    assert total["value"] == 2
    assert total["denominator_value"] == 1


def test_single_day_window_is_exactly_one_local_day(
    staff_factory, location_factory, provider_factory, appointment_type_factory, patient_factory
):
    owner = staff_factory(role="owner", slug="single-day-clinic")
    clinic = owner.clinic
    location = location_factory(clinic)
    provider = provider_factory(clinic, membership=owner.membership)
    appointment_type = appointment_type_factory(clinic)
    patient = patient_factory(clinic)

    def _create(start_at, status):
        return Appointment.all_objects.create(
            clinic=clinic,
            location=location,
            provider=provider,
            appointment_type=appointment_type,
            patient=patient,
            start_at=start_at,
            end_at=start_at + timedelta(minutes=30),
            status=status,
        )

    _create(local(KOLKATA, 2026, 6, 1, 0, 0), "completed")
    _create(local(KOLKATA, 2026, 6, 1, 23, 30), "completed")
    _create(local(KOLKATA, 2026, 6, 2, 0, 0), "cancelled")

    report = appointments.report(clinic, date(2026, 6, 1), date(2026, 6, 1))
    metrics = {item["key"]: item for item in report["metrics"]}
    assert metrics["appointments.total"]["value"] == 2
    assert metrics["appointments.completed"]["value"] == 2
    assert metrics["appointments.cancellation_rate"]["value"] == 0.0
    assert report["range"]["from"] == "2026-06-01"
    assert report["range"]["to"] == "2026-06-01"


def test_dst_fall_back_day_is_25_real_hours():
    from apps.analytics.metrics import clinic_timezone

    class _Clinic:
        timezone = "America/New_York"

    clinic = _Clinic()
    assert clinic_timezone(clinic) == NEW_YORK

    day = date(2026, 11, 1)
    start, end = resolve_range(clinic, day, day)
    # 2026-11-01 00:00 EDT is 04:00 UTC; 2026-11-02 00:00 EST is 05:00 UTC.
    assert start == datetime(2026, 11, 1, 4, 0, tzinfo=UTC)
    assert end == datetime(2026, 11, 2, 5, 0, tzinfo=UTC)
    assert end - start == timedelta(hours=25)
