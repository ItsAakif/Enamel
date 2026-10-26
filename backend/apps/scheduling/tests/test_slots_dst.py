"""DST behaviour of the slot engine (T110).

Documented policy (ARCHITECTURE.md section 9): rule boundaries are resolved with
``fold=0`` -- a wall-clock time that does not exist (spring-forward gap) is
skipped and an ambiguous one (fall-back overlap) resolves to its first
occurrence. Slots are stepped in clinic-local wall-clock time and each candidate
is then resolved to UTC, so the counts below are stable and hand-checkable.

America/New_York 2026: spring forward on 2026-03-08 (02:00 -> 03:00), fall back
on 2026-11-01 (02:00 -> 01:00). Both are Sundays, so a Sunday rule covers them.
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.scheduling.services.availability import available_minutes, compute_slots

from .conftest import NEW_YORK, utc

pytestmark = pytest.mark.django_db

SPRING_FORWARD = date(2026, 3, 8)
FALL_BACK = date(2026, 11, 1)
BEFORE_WINDOW = utc(2026, 1, 1, 0, 0)


@pytest.fixture
def dst_seed(seed_scheduling):
    def _seed(**type_extra):
        seed = seed_scheduling(**type_extra)
        clinic = seed["clinic"]
        clinic.timezone = "America/New_York"
        clinic.save(update_fields=["timezone"])
        return seed

    return _seed


def _local_labels(result) -> list[str]:
    return [
        slot["start"].astimezone(NEW_YORK).strftime("%H:%M") for slot in result["slots"]
    ]


def test_spring_forward_day_skips_the_missing_hour(dst_seed, rule_factory):
    seed = dst_seed(duration_min=15)
    rule_factory(
        seed["clinic"], seed["location"], weekday=6, start_time="00:00", end_time="04:00"
    )

    result = compute_slots(
        seed["clinic"],
        seed["appointment_type"],
        SPRING_FORWARD,
        SPRING_FORWARD,
        now=BEFORE_WINDOW,
    )

    # 16 wall-clock candidates minus the 4 that would start inside the gap and
    # the 01:45 slot that would end inside it.
    assert _local_labels(result) == [
        "00:00",
        "00:15",
        "00:30",
        "00:45",
        "01:00",
        "01:15",
        "01:30",
        "03:00",
        "03:15",
        "03:30",
        "03:45",
    ]
    assert len(result["slots"]) == 11
    # 00:00 EST == 05:00 UTC.
    assert result["slots"][0]["start"] == utc(2026, 3, 8, 0, 0, tz=NEW_YORK)
    # The day is genuinely one real hour shorter.
    assert available_minutes(seed["clinic"], SPRING_FORWARD, SPRING_FORWARD) == 180


def test_fall_back_day_offers_the_repeated_hour_once(dst_seed, rule_factory):
    seed = dst_seed(duration_min=15)
    rule_factory(
        seed["clinic"], seed["location"], weekday=6, start_time="00:00", end_time="04:00"
    )

    result = compute_slots(
        seed["clinic"],
        seed["appointment_type"],
        FALL_BACK,
        FALL_BACK,
        now=BEFORE_WINDOW,
    )

    assert _local_labels(result) == [
        "00:00",
        "00:15",
        "00:30",
        "00:45",
        "01:00",
        "01:15",
        "01:30",
        "01:45",
        "02:00",
        "02:15",
        "02:30",
        "02:45",
        "03:00",
        "03:15",
        "03:30",
        "03:45",
    ]
    assert len(result["slots"]) == 16
    # 00:00 EDT == 04:00 UTC.
    assert result["slots"][0]["start"] == utc(2026, 11, 1, 0, 0, tz=NEW_YORK)
    # The repeated hour is real time the provider is available for.
    assert available_minutes(seed["clinic"], FALL_BACK, FALL_BACK) == 300


def test_ordinary_rule_is_shifted_but_same_length_across_dst(dst_seed, rule_factory):
    seed = dst_seed(duration_min=15)
    rule_factory(
        seed["clinic"], seed["location"], weekday=6, start_time="09:00", end_time="12:00"
    )

    spring = compute_slots(
        seed["clinic"],
        seed["appointment_type"],
        SPRING_FORWARD,
        SPRING_FORWARD,
        now=BEFORE_WINDOW,
    )
    fall = compute_slots(
        seed["clinic"], seed["appointment_type"], FALL_BACK, FALL_BACK, now=BEFORE_WINDOW
    )

    assert len(spring["slots"]) == 12
    assert len(fall["slots"]) == 12
    # 09:00 EDT (after the spring transition) == 13:00 UTC.
    assert spring["slots"][0]["start"] == utc(2026, 3, 8, 9, 0, tz=NEW_YORK)
    assert spring["slots"][0]["start"].hour == 13
    # 09:00 EST (after the fall transition) == 14:00 UTC.
    assert fall["slots"][0]["start"] == utc(2026, 11, 1, 9, 0, tz=NEW_YORK)
    assert fall["slots"][0]["start"].hour == 14


def test_rule_starting_inside_the_gap_yields_no_slots(dst_seed, rule_factory):
    seed = dst_seed(duration_min=30)
    rule_factory(
        seed["clinic"], seed["location"], weekday=6, start_time="02:30", end_time="04:00"
    )

    result = compute_slots(
        seed["clinic"],
        seed["appointment_type"],
        SPRING_FORWARD,
        SPRING_FORWARD,
        now=BEFORE_WINDOW,
    )

    assert result["slots"] == []
