"""Fixtures shared by the messaging tests."""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from apps.core.tenancy import with_clinic
from apps.patients.models import Patient
from apps.patients.services.numbering import allocate_patient_number

KOLKATA = ZoneInfo("Asia/Kolkata")
#: 10:00 clinic-local: safely outside the 21:00-08:00 quiet window.
DAYTIME = datetime(2026, 1, 5, 10, 0, tzinfo=KOLKATA)
#: 22:00 clinic-local: inside the quiet window.
NIGHT = datetime(2026, 1, 5, 22, 0, tzinfo=KOLKATA)


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
def patient_factory(db):
    def _create(
        clinic,
        *,
        first_name: str = "Ada",
        last_name: str = "Lovelace",
        email: str = "ada@example.test",
        phone_e164: str = "+919876500000",
        status: str = Patient.Status.ACTIVE,
        **extra,
    ) -> Patient:
        with with_clinic(clinic):
            return Patient.all_objects.create(
                clinic=clinic,
                number=allocate_patient_number(clinic),
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone_e164=phone_e164,
                status=status,
                **extra,
            )

    return _create


@pytest.fixture
def templates(db):
    from apps.messaging.seed import ensure_default_templates

    def _ensure(clinic):
        return ensure_default_templates(clinic)

    return _ensure


@pytest.fixture
def grant_consent(db):
    from apps.messaging.services import consent as consent_service

    def _grant(patient, channel: str = "email", purpose: str = "appointment_reminders", **kw):
        return consent_service.grant(patient, channel, purpose, **kw)

    return _grant
