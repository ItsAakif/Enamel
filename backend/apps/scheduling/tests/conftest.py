"""Fixtures shared by the scheduling tests."""

from __future__ import annotations

import uuid
from datetime import UTC, time
from zoneinfo import ZoneInfo

import pytest

from apps.clinics.models import Location, Provider
from apps.patients.models import Patient
from apps.patients.services.numbering import allocate_patient_number
from apps.scheduling.models import Appointment, AppointmentType, AvailabilityRule, Operatory

KOLKATA = ZoneInfo("Asia/Kolkata")
NEW_YORK = ZoneInfo("America/New_York")


class StaffClient:
    """An authenticated API client bound to a clinic and role."""

    def __init__(self, client, user, clinic, membership, role: str) -> None:
        self.client = client
        self.user = user
        self.clinic = clinic
        self.membership = membership
        self.role = role

    @property
    def clinic_header(self) -> dict:
        return {"HTTP_X_CLINIC_ID": str(self.clinic.id)}

    def _kwargs(self, kwargs: dict) -> dict:
        kwargs.setdefault("HTTP_X_CLINIC_ID", str(self.clinic.id))
        return kwargs

    def get(self, url, params=None, **kwargs):
        return self.client.get(url, params, **self._kwargs(kwargs))

    def post(self, url, data=None, **kwargs):
        kwargs.setdefault("format", "json")
        return self.client.post(url, data, **self._kwargs(kwargs))

    def patch(self, url, data=None, **kwargs):
        kwargs.setdefault("format", "json")
        return self.client.patch(url, data, **self._kwargs(kwargs))

    def put(self, url, data=None, **kwargs):
        kwargs.setdefault("format", "json")
        return self.client.put(url, data, **self._kwargs(kwargs))

    def delete(self, url, **kwargs):
        return self.client.delete(url, **self._kwargs(kwargs))


@pytest.fixture
def staff_factory(user_factory, clinic_factory, membership_factory):
    """Create ``(client, user, clinic, membership, role)`` bundles on demand."""

    def _create(
        clinic=None,
        role: str = "owner",
        slug: str | None = None,
        email: str | None = None,
        **clinic_extra,
    ):
        from rest_framework.test import APIClient

        suffix = uuid.uuid4().hex[:8]
        client = APIClient()
        user = user_factory(email or f"{role}-{suffix}@enamel.test")
        if clinic is None:
            clinic = clinic_factory(slug=slug or f"clinic-{suffix}", **clinic_extra)
        membership = membership_factory(clinic, user, role=role)
        client.force_login(user)
        return StaffClient(client, user, clinic, membership, role)

    return _create


@pytest.fixture
def owner(staff_factory):
    return staff_factory(role="owner")


@pytest.fixture
def assistant(staff_factory):
    return staff_factory(role="assistant")


@pytest.fixture
def location_factory(db):
    def _create(clinic, *, name: str = "Main", **extra) -> Location:
        return Location.all_objects.create(clinic=clinic, name=name, **extra)

    return _create


@pytest.fixture
def provider_factory(db, user_factory, membership_factory):
    def _create(
        clinic,
        *,
        display_name: str = "Dr Test",
        membership=None,
        role: str = "dentist",
        **extra,
    ) -> Provider:
        if membership is None:
            suffix = uuid.uuid4().hex[:8]
            user = user_factory(f"prov-{suffix}@enamel.test")
            membership = membership_factory(clinic, user, role=role)
        return Provider.all_objects.create(
            clinic=clinic,
            membership=membership,
            display_name=display_name,
            **extra,
        )

    return _create


@pytest.fixture
def appointment_type_factory(db):
    def _create(
        clinic,
        *,
        code: str | None = None,
        name: str = "Checkup",
        duration_min: int = 30,
        **extra,
    ) -> AppointmentType:
        return AppointmentType.all_objects.create(
            clinic=clinic,
            code=code or f"type-{uuid.uuid4().hex[:6]}",
            name=name,
            duration_min=duration_min,
            **extra,
        )

    return _create


@pytest.fixture
def operatory_factory(db):
    def _create(clinic, location, *, name: str = "Chair 1", **extra) -> Operatory:
        return Operatory.all_objects.create(
            clinic=clinic,
            location=location,
            name=name,
            **extra,
        )

    return _create


@pytest.fixture
def patient_factory(db):
    def _create(
        clinic,
        *,
        number: str | None = None,
        first_name: str = "Ada",
        last_name: str = "Lovelace",
        **extra,
    ) -> Patient:
        return Patient.all_objects.create(
            clinic=clinic,
            number=number or allocate_patient_number(clinic),
            first_name=first_name,
            last_name=last_name,
            **extra,
        )

    return _create


@pytest.fixture
def rule_factory(db):
    def _create(
        clinic,
        location,
        *,
        kind: str = AvailabilityRule.Kind.WORKING,
        weekday: int = 0,
        start_time=time(9, 0),
        end_time=time(17, 0),
        provider=None,
        **extra,
    ) -> AvailabilityRule:
        if isinstance(start_time, str):
            start_time = time.fromisoformat(start_time)
        if isinstance(end_time, str):
            end_time = time.fromisoformat(end_time)
        return AvailabilityRule.all_objects.create(
            clinic=clinic,
            location=location,
            kind=kind,
            weekday=weekday,
            start_time=start_time,
            end_time=end_time,
            provider=provider,
            **extra,
        )

    return _create


@pytest.fixture
def seed_scheduling(
    owner, location_factory, provider_factory, appointment_type_factory, patient_factory
):
    """A minimal bookable setup: location, provider, type and patient."""

    def _seed(*, duration_min: int = 30, **type_extra):
        clinic = owner.clinic
        location = location_factory(clinic, name="Main")
        provider = provider_factory(clinic, membership=owner.membership, display_name="Dr Owner")
        appointment_type = appointment_type_factory(
            clinic, duration_min=duration_min, **type_extra
        )
        patient = patient_factory(clinic)
        return {
            "clinic": clinic,
            "location": location,
            "provider": provider,
            "appointment_type": appointment_type,
            "patient": patient,
        }

    return _seed


def utc(y: int, m: int, d: int, hh: int, mm: int, tz=KOLKATA):
    """A UTC instant for a clinic-local wall-clock time."""

    from datetime import datetime

    return datetime(y, m, d, hh, mm, tzinfo=tz).astimezone(UTC)


def make_appointment(seed, start, *, end=None, status="scheduled", **extra) -> Appointment:
    """Create an appointment directly (bypassing the service) for setup."""

    from datetime import timedelta

    appointment_type = extra.pop("appointment_type", seed["appointment_type"])
    if end is None:
        end = start + timedelta(minutes=appointment_type.duration_min)
    return Appointment.all_objects.create(
        clinic=extra.pop("clinic", seed["clinic"]),
        location=extra.pop("location", seed["location"]),
        patient=extra.pop("patient", seed["patient"]),
        provider=extra.pop("provider", seed["provider"]),
        appointment_type=appointment_type,
        start_at=start,
        end_at=end,
        status=status,
        **extra,
    )
