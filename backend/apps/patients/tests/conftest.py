"""Fixtures shared by the patient-domain tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from apps.patients.models import Patient
from apps.patients.services.numbering import allocate_patient_number


@pytest.fixture
def patient_factory(db):
    """Create a patient directly (bypassing the API) for setup-heavy tests."""

    def _create(
        clinic,
        *,
        number: str | None = None,
        first_name: str = "Ada",
        last_name: str = "Lovelace",
        **extra,
    ) -> Patient:
        if number is None:
            number = allocate_patient_number(clinic)
        return Patient.all_objects.create(
            clinic=clinic,
            number=number,
            first_name=first_name,
            last_name=last_name,
            **extra,
        )

    return _create


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

    def _create(clinic=None, role: str = "owner", slug: str | None = None, email: str | None = None):
        from rest_framework.test import APIClient

        suffix = uuid.uuid4().hex[:8]
        client = APIClient()
        user = user_factory(email or f"{role}-{suffix}@bright.test")
        if clinic is None:
            clinic = clinic_factory(slug=slug or f"clinic-{suffix}")
        membership = membership_factory(clinic, user, role=role)
        client.force_login(user)
        return StaffClient(client, user, clinic, membership, role)

    return _create


@pytest.fixture
def owner(staff_factory):
    return staff_factory(role="owner")


def patient_payload(**overrides) -> dict:
    payload = {
        "first_name": "Grace",
        "last_name": "Hopper",
        "date_of_birth": "1990-05-04",
        "sex_at_birth": "female",
        "phone": "+91 98765 43210",
        "email": "Grace.Hopper@example.in",
    }
    payload.update(overrides)
    return payload


class _FakeAppointmentManager:
    """Minimal stand-in for a scheduling manager used by the guarded lookup."""

    def __init__(self, exists: bool) -> None:
        self._exists = exists

    def filter(self, **kwargs):
        return self

    def exists(self) -> bool:
        return self._exists


@pytest.fixture
def fake_scheduling(monkeypatch):
    """Pretend the scheduling app is installed, with a controllable lookup.

    ``installed=False`` simulates a project without the scheduling app (the
    opaque-reference branch); T110 ships scheduling, so that state can no longer
    be observed for real.
    """

    def _install(exists: bool, *, installed: bool = True):
        from apps.patients.services import encounters

        class _Registry:
            @staticmethod
            def is_installed(name: str) -> bool:
                return installed and name == "apps.scheduling"

            @staticmethod
            def get_model(app_label: str, model_name: str):
                if (app_label, model_name) != ("scheduling", "Appointment"):
                    raise LookupError(model_name)
                return SimpleNamespace(all_objects=_FakeAppointmentManager(exists))

        monkeypatch.setattr(encounters, "django_apps", _Registry)
        return _Registry

    return _install
