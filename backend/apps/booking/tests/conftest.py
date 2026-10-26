"""Fixtures for the public booking tests.

A booking needs a clinic with a timezone, one active location, a bookable
provider, a ``public_bookable`` appointment type and an availability rule that
covers the day being booked. The helpers here build that set once and expose
deterministic future slots so the tests never depend on the wall clock.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.clinics.models import Location, Provider
from apps.scheduling.models import AppointmentType, AvailabilityRule

KOLKATA = ZoneInfo("Asia/Kolkata")

#: A phone number that parses as a valid E.164 number for the default region.
VALID_PHONE = "+919876543210"


def utc_slot(days: int = 7, hour: int = 9, minute: int = 0) -> datetime:
    """A UTC instant for a clinic-local wall-clock time ``days`` from today."""

    target = timezone.localdate() + timedelta(days=days)
    naive = datetime.combine(target, time(hour, minute))
    return naive.replace(tzinfo=KOLKATA).astimezone(UTC)


def booking_payload(seed: dict[str, Any], slot_start: datetime, **overrides: Any) -> dict[str, Any]:
    """A valid ``POST /public/bookings/`` body for ``seed``."""

    payload: dict[str, Any] = {
        "appointment_type": str(seed["appointment_type"].pk),
        "location": str(seed["location"].pk),
        "slot_start": slot_start.isoformat(),
        "patient": {
            "first_name": "Nina",
            "last_name": "New",
            "date_of_birth": "1990-05-04",
            "phone": VALID_PHONE,
            "email": "nina@example.test",
        },
        "consents": {"booking_terms": True, "privacy": True},
        "idempotency_key": f"key-{uuid.uuid4().hex[:12]}",
        "website": "",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def seed(db, clinic_factory, user_factory, membership_factory) -> dict[str, Any]:
    """A bookable clinic: location, provider, public type, 7-day 09:00-17:00 rule."""

    suffix = uuid.uuid4().hex[:6]
    clinic = clinic_factory(slug=f"public-{suffix}")
    location = Location.all_objects.create(clinic=clinic, name="Main")
    for weekday in range(7):
        AvailabilityRule.all_objects.create(
            clinic=clinic,
            location=location,
            kind=AvailabilityRule.Kind.WORKING,
            weekday=weekday,
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
    staff_user = user_factory(f"staff-{suffix}@enamel.test")
    membership = membership_factory(clinic, staff_user, role="dentist")
    provider = Provider.all_objects.create(
        clinic=clinic,
        membership=membership,
        display_name="Dr Public",
        specialty="General",
        color="#123456",
        is_bookable=True,
    )
    appointment_type = AppointmentType.all_objects.create(
        clinic=clinic,
        code="checkup",
        name="Checkup",
        duration_min=30,
        public_bookable=True,
        is_active=True,
    )
    return {
        "clinic": clinic,
        "location": location,
        "provider": provider,
        "appointment_type": appointment_type,
        "staff_user": staff_user,
        "membership": membership,
    }


@pytest.fixture
def staff_api(api_client, seed):
    """An authenticated API client scoped to the seeded clinic."""

    api_client.force_login(seed["staff_user"])
    return api_client
