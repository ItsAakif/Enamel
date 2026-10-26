"""Fixtures shared by the interop tests (T130)."""

from __future__ import annotations

import uuid

import pytest

from apps.patients.models import Patient
from apps.patients.services.numbering import allocate_patient_number


class StaffClient:
    """An authenticated API client bound to a clinic and role."""

    def __init__(self, client, user, clinic, membership, role: str) -> None:
        self.client = client
        self.user = user
        self.clinic = clinic
        self.membership = membership
        self.role = role

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


@pytest.fixture
def staff_factory(user_factory, clinic_factory, membership_factory):
    def _create(clinic=None, role: str = "owner", slug: str | None = None, email: str | None = None):
        from rest_framework.test import APIClient

        suffix = uuid.uuid4().hex[:8]
        client = APIClient()
        user = user_factory(email or f"{role}-{suffix}@interop.test")
        if clinic is None:
            clinic = clinic_factory(slug=slug or f"interop-clinic-{suffix}")
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
def patient_factory(db):
    def _create(clinic, *, first_name: str = "Ada", last_name: str = "Lovelace", **extra) -> Patient:
        return Patient.all_objects.create(
            clinic=clinic,
            number=allocate_patient_number(clinic),
            first_name=first_name,
            last_name=last_name,
            **extra,
        )

    return _create


@pytest.fixture
def scheduling_deps(db):
    """Create the provider/location/appointment-type an appointment import needs."""

    def _create(clinic, user, *, provider_name: str = "Dr Ada", type_code: str = "exam"):
        from apps.clinics.models import Location, Membership, Provider
        from apps.scheduling.models import AppointmentType

        location = Location.all_objects.create(clinic=clinic, name="Main location")
        membership = Membership.all_objects.get(clinic=clinic, user=user)
        provider = Provider.all_objects.create(
            clinic=clinic, membership=membership, display_name=provider_name
        )
        appointment_type = AppointmentType.all_objects.create(
            clinic=clinic, code=type_code, name="Exam", duration_min=30
        )
        return {
            "location": location,
            "provider": provider,
            "appointment_type": appointment_type,
        }

    return _create


@pytest.fixture
def connector_factory(db):
    def _create(clinic, *, settings_json=None, mode: str = "simulator", **extra):
        from apps.interop.models import ConnectorConfig

        return ConnectorConfig.all_objects.create(
            clinic=clinic,
            kind="open_dental",
            name=extra.pop("name", "Open Dental"),
            mode=mode,
            settings_json=settings_json or {},
            **extra,
        )

    return _create
