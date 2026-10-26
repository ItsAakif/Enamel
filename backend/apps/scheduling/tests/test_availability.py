"""Slot generation: hours, breaks, exceptions, buffers, lead time, window (T110)."""

from __future__ import annotations

from datetime import date, time
from zoneinfo import ZoneInfo

import pytest

from apps.core.errors import ApiError
from apps.scheduling.services.availability import compute_slots

from .conftest import make_appointment, utc

pytestmark = pytest.mark.django_db

UTC = ZoneInfo("UTC")

MONDAY = date(2026, 1, 5)
BEFORE_WINDOW = utc(2026, 1, 1, 0, 0)


def _starts(result) -> list:
    return [slot["start"] for slot in result["slots"]]


def test_working_rule_expands_to_step_aligned_slots(seed_scheduling, rule_factory):
    seed = seed_scheduling(duration_min=30)
    rule_factory(
        seed["clinic"], seed["location"], weekday=0, start_time="09:00", end_time="11:00"
    )

    result = compute_slots(
        seed["clinic"], seed["appointment_type"], MONDAY, MONDAY, now=BEFORE_WINDOW
    )

    assert result["window"]["step_min"] == 15
    assert result["window"]["max_days"] == 31
    assert result["window"]["days"] == 1
    assert _starts(result) == [
        utc(2026, 1, 5, 9, 0),
        utc(2026, 1, 5, 9, 15),
        utc(2026, 1, 5, 9, 30),
        utc(2026, 1, 5, 9, 45),
        utc(2026, 1, 5, 10, 0),
        utc(2026, 1, 5, 10, 15),
        utc(2026, 1, 5, 10, 30),
    ]
    # 09:00 Asia/Kolkata == 03:30 UTC.
    assert result["slots"][0]["end"] == utc(2026, 1, 5, 9, 30)
    assert result["slots"][0]["reason"] == "available"


def test_break_rule_removes_overlapping_slots(seed_scheduling, rule_factory):
    seed = seed_scheduling(duration_min=30)
    rule_factory(
        seed["clinic"], seed["location"], weekday=0, start_time="09:00", end_time="11:00"
    )
    rule_factory(
        seed["clinic"],
        seed["location"],
        kind="break",
        weekday=0,
        start_time="10:00",
        end_time="10:30",
    )

    result = compute_slots(
        seed["clinic"], seed["appointment_type"], MONDAY, MONDAY, now=BEFORE_WINDOW
    )

    assert _starts(result) == [
        utc(2026, 1, 5, 9, 0),
        utc(2026, 1, 5, 9, 15),
        utc(2026, 1, 5, 9, 30),
        utc(2026, 1, 5, 10, 30),
    ]
    assert result["window"]["reasons"]["break_minutes"] == 30


def test_block_rule_removes_overlapping_slots(seed_scheduling, rule_factory):
    seed = seed_scheduling(duration_min=30)
    rule_factory(
        seed["clinic"], seed["location"], weekday=0, start_time="09:00", end_time="11:00"
    )
    rule_factory(
        seed["clinic"],
        seed["location"],
        kind="block",
        weekday=0,
        start_time="09:00",
        end_time="09:45",
    )

    result = compute_slots(
        seed["clinic"], seed["appointment_type"], MONDAY, MONDAY, now=BEFORE_WINDOW
    )

    assert _starts(result) == [
        utc(2026, 1, 5, 9, 45),
        utc(2026, 1, 5, 10, 0),
        utc(2026, 1, 5, 10, 15),
        utc(2026, 1, 5, 10, 30),
    ]


def test_all_day_exception_closes_the_day(seed_scheduling, rule_factory):
    from apps.scheduling.models import ScheduleException

    seed = seed_scheduling(duration_min=30)
    rule_factory(
        seed["clinic"], seed["location"], weekday=0, start_time="09:00", end_time="11:00"
    )
    ScheduleException.all_objects.create(
        clinic=seed["clinic"],
        location=seed["location"],
        date=MONDAY,
        all_day=True,
        kind="holiday",
        reason="Public holiday",
    )

    result = compute_slots(
        seed["clinic"], seed["appointment_type"], MONDAY, MONDAY, now=BEFORE_WINDOW
    )

    assert result["slots"] == []
    assert result["window"]["reasons"]["holiday"] == 1


def test_partial_exception_shortens_the_day(seed_scheduling, rule_factory):
    from apps.scheduling.models import ScheduleException

    seed = seed_scheduling(duration_min=30)
    rule_factory(
        seed["clinic"], seed["location"], weekday=0, start_time="09:00", end_time="11:00"
    )
    ScheduleException.all_objects.create(
        clinic=seed["clinic"],
        location=seed["location"],
        date=MONDAY,
        all_day=False,
        start_time=time(9, 30),
        end_time=time(10, 30),
        kind="training",
    )

    result = compute_slots(
        seed["clinic"], seed["appointment_type"], MONDAY, MONDAY, now=BEFORE_WINDOW
    )

    assert _starts(result) == [
        utc(2026, 1, 5, 9, 0),
        utc(2026, 1, 5, 10, 30),
    ]
    assert result["window"]["reasons"]["exception_minutes"] == 60


def test_existing_appointment_and_buffer_exclude_slots(seed_scheduling, rule_factory):
    seed = seed_scheduling(duration_min=30, buffer_after_min=15)
    rule_factory(
        seed["clinic"], seed["location"], weekday=0, start_time="09:00", end_time="11:00"
    )
    make_appointment(seed, utc(2026, 1, 5, 9, 0), status="scheduled")

    result = compute_slots(
        seed["clinic"], seed["appointment_type"], MONDAY, MONDAY, now=BEFORE_WINDOW
    )

    # The booked slot plus every slot whose 15-minute tail touches it is gone.
    assert _starts(result) == [
        utc(2026, 1, 5, 9, 45),
        utc(2026, 1, 5, 10, 0),
        utc(2026, 1, 5, 10, 15),
        utc(2026, 1, 5, 10, 30),
    ]
    assert result["window"]["reasons"]["booked"] == 3


def test_cancelled_appointment_releases_the_slot(seed_scheduling, rule_factory):
    seed = seed_scheduling(duration_min=30)
    rule_factory(
        seed["clinic"], seed["location"], weekday=0, start_time="09:00", end_time="10:00"
    )
    make_appointment(seed, utc(2026, 1, 5, 9, 0), status="cancelled")

    result = compute_slots(
        seed["clinic"], seed["appointment_type"], MONDAY, MONDAY, now=BEFORE_WINDOW
    )

    assert utc(2026, 1, 5, 9, 0) in _starts(result)


def test_lead_time_excludes_near_slots(owner, location_factory, provider_factory, appointment_type_factory):
    clinic = owner.clinic
    clinic.timezone = "UTC"
    clinic.save(update_fields=["timezone"])
    location = location_factory(clinic)
    provider_factory(clinic, membership=owner.membership)
    appointment_type = appointment_type_factory(clinic, duration_min=30)

    from apps.scheduling.models import AvailabilityRule

    AvailabilityRule.all_objects.create(
        clinic=clinic,
        location=location,
        kind="working",
        weekday=0,
        start_time=time(9, 0),
        end_time=time(11, 0),
    )

    # 10:00 now + 2h lead time == 12:00, past the whole window.
    result = compute_slots(
        clinic,
        appointment_type,
        MONDAY,
        MONDAY,
        now=utc(2026, 1, 5, 10, 0, tz=UTC),
    )
    assert result["slots"] == []
    assert result["window"]["reasons"]["lead_time"] == 7
    assert result["window"]["lead_time_hours"] == 2.0


def test_requires_operatory_blocks_when_no_chair_is_free(
    seed_scheduling, rule_factory, operatory_factory, provider_factory, patient_factory
):
    seed = seed_scheduling(duration_min=30, requires_operatory=True)
    rule_factory(
        seed["clinic"], seed["location"], weekday=0, start_time="09:00", end_time="11:00"
    )
    chair = operatory_factory(seed["clinic"], seed["location"], name="Chair 1")
    other_provider = provider_factory(seed["clinic"], display_name="Dr Other")
    other_patient = patient_factory(seed["clinic"])
    make_appointment(
        seed,
        utc(2026, 1, 5, 9, 0),
        provider=other_provider,
        patient=other_patient,
        operatory=chair,
        status="scheduled",
    )

    result = compute_slots(
        seed["clinic"],
        seed["appointment_type"],
        MONDAY,
        MONDAY,
        provider=seed["provider"],
        now=BEFORE_WINDOW,
    )

    assert utc(2026, 1, 5, 9, 0) not in _starts(result)
    assert result["window"]["reasons"]["operatory_unavailable"] == 2

    operatory_factory(seed["clinic"], seed["location"], name="Chair 2")
    result = compute_slots(
        seed["clinic"],
        seed["appointment_type"],
        MONDAY,
        MONDAY,
        provider=seed["provider"],
        now=BEFORE_WINDOW,
    )
    assert len(result["slots"]) == 7
    assert all(slot["operatory"] is not None for slot in result["slots"])


def test_no_rule_reports_reason(seed_scheduling):
    seed = seed_scheduling(duration_min=30)

    result = compute_slots(
        seed["clinic"], seed["appointment_type"], MONDAY, MONDAY, now=BEFORE_WINDOW
    )

    assert result["slots"] == []
    assert result["window"]["reasons"]["no_rule"] == 1


def test_provider_rule_wins_over_clinic_wide_default(seed_scheduling, rule_factory):
    seed = seed_scheduling(duration_min=30)
    rule_factory(
        seed["clinic"],
        seed["location"],
        weekday=0,
        start_time="09:00",
        end_time="17:00",
        provider=None,
    )
    rule_factory(
        seed["clinic"],
        seed["location"],
        weekday=0,
        start_time="09:00",
        end_time="11:00",
        provider=seed["provider"],
    )

    result = compute_slots(
        seed["clinic"], seed["appointment_type"], MONDAY, MONDAY, now=BEFORE_WINDOW
    )

    assert len(result["slots"]) == 7


def test_window_wider_than_31_days_is_a_400(seed_scheduling):
    seed = seed_scheduling(duration_min=30)

    with pytest.raises(ApiError) as excinfo:
        compute_slots(
            seed["clinic"],
            seed["appointment_type"],
            date(2026, 1, 1),
            date(2026, 2, 1),
            now=BEFORE_WINDOW,
        )
    assert excinfo.value.status_code == 400
    assert excinfo.value.code == "validation_error"

    # Exactly 31 days is allowed.
    result = compute_slots(
        seed["clinic"],
        seed["appointment_type"],
        date(2026, 1, 1),
        date(2026, 1, 31),
        now=BEFORE_WINDOW,
    )
    assert result["window"]["days"] == 31


def test_reversed_range_is_a_400(seed_scheduling):
    seed = seed_scheduling(duration_min=30)
    with pytest.raises(ApiError):
        compute_slots(
            seed["clinic"],
            seed["appointment_type"],
            date(2026, 1, 10),
            date(2026, 1, 1),
            now=BEFORE_WINDOW,
        )
