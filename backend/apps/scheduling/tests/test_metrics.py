"""Operational metrics against hand-computed values (T110).

Seed: June 2026 (2026-06-01 is a Monday) with a Monday-Friday 09:00-12:00
working rule (180 minutes/day). June 2026 has 22 weekdays, so the availability
denominator is 22 * 180 = 3960 minutes.

Seven in-window appointments (30 minutes each, one provider):
    3 completed, 1 cancelled, 1 no_show, 1 scheduled, 1 confirmed
plus one appointment for patient 2 *before* the window (making them returning).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from apps.scheduling.services.metrics import metrics

from .conftest import make_appointment, utc

pytestmark = pytest.mark.django_db

MONTH_START = date(2026, 6, 1)
MONTH_END = date(2026, 6, 30)
EXPECTED_AVAILABLE_MINUTES = 22 * 180


@pytest.fixture
def seeded_month(seed_scheduling, rule_factory, patient_factory):
    seed = seed_scheduling(duration_min=30)
    for weekday in range(5):  # Monday-Friday
        rule_factory(
            seed["clinic"],
            seed["location"],
            weekday=weekday,
            start_time="09:00",
            end_time="12:00",
        )

    p1 = seed["patient"]
    p2 = patient_factory(seed["clinic"], first_name="Grace")
    p3 = patient_factory(seed["clinic"], first_name="Alan")

    # Patient 1 (new): first ever appointment is inside the window.
    make_appointment(seed, utc(2026, 6, 1, 9, 0), patient=p1, status="completed")
    make_appointment(seed, utc(2026, 6, 2, 9, 0), patient=p1, status="completed")
    make_appointment(seed, utc(2026, 6, 3, 9, 0), patient=p1, status="cancelled")
    # Patient 2 (returning): an appointment before the window exists.
    make_appointment(seed, utc(2026, 5, 20, 9, 0), patient=p2, status="completed")
    make_appointment(seed, utc(2026, 6, 1, 10, 0), patient=p2, status="completed")
    make_appointment(seed, utc(2026, 6, 2, 10, 0), patient=p2, status="scheduled")
    # Patient 3 (new).
    make_appointment(seed, utc(2026, 6, 4, 9, 0), patient=p3, status="no_show")
    make_appointment(seed, utc(2026, 6, 5, 9, 0), patient=p3, status="confirmed")

    seed["p2"] = p2
    seed["p3"] = p3
    return seed


def test_metrics_match_hand_computed_values(seeded_month):
    result = metrics(seeded_month["clinic"], MONTH_START, MONTH_END)

    assert result["appointments"]["total"] == 7
    assert result["appointments"]["by_status"] == {
        "requested": 0,
        "held": 0,
        "scheduled": 1,
        "confirmed": 1,
        "checked_in": 0,
        "in_progress": 0,
        "completed": 3,
        "cancelled": 1,
        "no_show": 1,
    }

    assert result["completed"] == 3
    assert result["cancelled"] == 1
    assert result["no_show"] == 1
    assert result["completion_rate"] == round(3 / 7, 4)
    assert result["cancellation_rate"] == round(1 / 7, 4)
    assert result["no_show_rate"] == round(1 / 7, 4)

    # 5 consuming appointments * 30 minutes.
    assert result["booked_minutes"] == 150
    assert result["available_minutes"] == EXPECTED_AVAILABLE_MINUTES
    assert result["utilisation"] == round(150 / EXPECTED_AVAILABLE_MINUTES, 4)

    assert result["patients"] == {"distinct": 3, "new": 2, "returning": 1}
    assert result["definition"]["utilisation"] == "booked_minutes / available_minutes"


def test_pre_window_appointment_is_outside_the_window(seeded_month):
    # May 2026 alone has no working rule in this clinic seed beyond the ones
    # created for June? They are weekday-based, so May weekdays also expand.
    may = metrics(seeded_month["clinic"], date(2026, 5, 1), date(2026, 5, 31))
    assert may["appointments"]["total"] == 1
    assert may["completed"] == 1
    assert may["patients"]["new"] == 1
    assert may["patients"]["returning"] == 0


def test_break_rules_reduce_available_minutes(seeded_month, rule_factory):
    for weekday in range(5):
        rule_factory(
            seeded_month["clinic"],
            seeded_month["location"],
            kind="break",
            weekday=weekday,
            start_time="10:00",
            end_time="10:30",
        )

    result = metrics(seeded_month["clinic"], MONTH_START, MONTH_END)

    assert result["available_minutes"] == 22 * (180 - 30)
    # Booked minutes are unchanged: a break never rewrites what was booked.
    assert result["booked_minutes"] == 150


def test_all_day_exception_removes_a_day(seeded_month):
    from apps.scheduling.models import ScheduleException

    ScheduleException.all_objects.create(
        clinic=seeded_month["clinic"],
        location=seeded_month["location"],
        date=date(2026, 6, 1),
        all_day=True,
        kind="holiday",
    )

    result = metrics(seeded_month["clinic"], MONTH_START, MONTH_END)

    assert result["available_minutes"] == 21 * 180


def test_metrics_are_location_scoped(seeded_month, location_factory, provider_factory):
    other_location = location_factory(seeded_month["clinic"], name="Second")
    other_provider = provider_factory(seeded_month["clinic"], display_name="Dr Second")
    make_appointment(
        seeded_month,
        utc(2026, 6, 8, 9, 0),
        location=other_location,
        provider=other_provider,
        status="completed",
    )

    overall = metrics(seeded_month["clinic"], MONTH_START, MONTH_END)
    scoped = metrics(
        seeded_month["clinic"], MONTH_START, MONTH_END, location=other_location
    )

    assert overall["appointments"]["total"] == 8
    assert scoped["appointments"]["total"] == 1
    assert scoped["completed"] == 1
    # The second location has no working rules, so its denominator is zero.
    assert scoped["available_minutes"] == 0
    assert scoped["utilisation"] == 0.0


def test_empty_window_is_all_zeroes(seed_scheduling):
    seed = seed_scheduling(duration_min=30)
    result = metrics(seed["clinic"], date(2030, 1, 1), date(2030, 1, 31))

    assert result["appointments"]["total"] == 0
    assert result["completion_rate"] == 0.0
    assert result["cancellation_rate"] == 0.0
    assert result["no_show_rate"] == 0.0
    assert result["utilisation"] == 0.0
    assert result["patients"] == {"distinct": 0, "new": 0, "returning": 0}


def test_metrics_endpoint_returns_the_same_numbers(owner, seeded_month):
    response = owner.get(
        "/api/v1/scheduling/metrics/",
        {"from": MONTH_START.isoformat(), "to": MONTH_END.isoformat()},
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["appointments"]["total"] == 7
    assert body["available_minutes"] == EXPECTED_AVAILABLE_MINUTES
    assert body["patients"]["returning"] == 1


def test_metrics_endpoint_requires_both_bounds(owner, seeded_month):
    response = owner.get("/api/v1/scheduling/metrics/", {"from": "2026-06-01"})
    assert response.status_code == 400
    assert "to" in response.json()["error"]["field_errors"]


def test_metrics_endpoint_rejects_a_reversed_range(owner, seeded_month):
    response = owner.get(
        "/api/v1/scheduling/metrics/", {"from": "2026-06-30", "to": "2026-06-01"}
    )
    assert response.status_code == 400


def test_metrics_denominator_counts_each_provider_lane(
    seeded_month, provider_factory
):
    provider_factory(seeded_month["clinic"], display_name="Dr Extra")

    result = metrics(seeded_month["clinic"], MONTH_START, MONTH_END)

    # The extra provider has no rules of its own but inherits the clinic-wide
    # ones, so the denominator counts a second lane.
    assert result["available_minutes"] == 2 * EXPECTED_AVAILABLE_MINUTES


def test_metrics_window_is_half_open(seeded_month):
    result = metrics(seeded_month["clinic"], date(2026, 6, 1), date(2026, 6, 1))
    assert result["appointments"]["total"] == 2
    assert result["window"]["from"] == "2026-06-01"
    assert result["window"]["to"] == "2026-06-01"
    # 06-01 10:00 is inside; 06-01 09:00 is inside.
    assert result["booked_minutes"] == 60


def test_booked_minutes_ignore_cancelled_and_no_show(seeded_month):
    result = metrics(seeded_month["clinic"], MONTH_START, MONTH_END)
    # 3 completed + 1 scheduled + 1 confirmed.
    assert result["booked_minutes"] == 5 * 30


def test_window_length_is_reported(seeded_month):
    result = metrics(seeded_month["clinic"], MONTH_START, MONTH_END)
    assert result["window"]["timezone"] == "Asia/Kolkata"
    assert result["window"]["end_at"] - result["window"]["start_at"] == timedelta(days=30)
