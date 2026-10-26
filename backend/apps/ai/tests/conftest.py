"""Fixtures shared by the AI tests."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from apps.ai.providers.base import Message, request_hash
from apps.ai.providers.mock import MockProvider


@pytest.fixture
def mock_provider() -> MockProvider:
    return MockProvider()


@pytest.fixture
def staff_factory(user_factory, clinic_factory, membership_factory):
    """Authenticated API client bound to a clinic and role (mirrors messaging)."""

    def _create(clinic=None, role: str = "owner", slug: str | None = None, email: str | None = None, **extra):
        from rest_framework.test import APIClient

        suffix = uuid.uuid4().hex[:8]
        client = APIClient()
        user = user_factory(email or f"{role}-{suffix}@enamel.test")
        if clinic is None:
            clinic = clinic_factory(slug=slug or f"clinic-{suffix}", **extra)
        membership = membership_factory(clinic, user, role=role)
        client.force_login(user)
        return _StaffClient(client, user, clinic, membership, role)

    return _create


class _StaffClient:
    def __init__(self, client, user, clinic, membership, role):
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
def owner(staff_factory):
    return staff_factory(role="owner")


@pytest.fixture
def dentist(staff_factory):
    return staff_factory(role="dentist")


@pytest.fixture
def assistant(staff_factory):
    return staff_factory(role="assistant")


@pytest.fixture
def patient_factory(db):
    from apps.core.tenancy import with_clinic
    from apps.patients.models import Patient
    from apps.patients.services.numbering import allocate_patient_number

    def _create(clinic, *, first_name="Ada", last_name="Lovelace", email="ada@example.test",
                phone_e164="+919876500000", date_of_birth=None, **extra):
        with with_clinic(clinic):
            return Patient.all_objects.create(
                clinic=clinic,
                number=allocate_patient_number(clinic),
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone_e164=phone_e164,
                date_of_birth=date_of_birth,
                **extra,
            )

    return _create


def canonical_hash(messages, **kwargs) -> str:
    return request_hash([Message(role="user", content=text) for text in messages], **kwargs)


# --------------------------------------------------------------------------- #
# Scheduling fixtures for the tool and voice tests
# --------------------------------------------------------------------------- #

KOLKATA = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def location_factory(db):
    from apps.clinics.models import Location

    def _create(clinic, *, name: str = "Main", **extra) -> Location:
        return Location.all_objects.create(clinic=clinic, name=name, **extra)

    return _create


@pytest.fixture
def provider_factory(db, user_factory, membership_factory):
    from apps.clinics.models import Provider

    def _create(clinic, *, display_name="Dr Test", membership=None, role="dentist", **extra):
        if membership is None:
            suffix = uuid.uuid4().hex[:8]
            user = user_factory(f"prov-{suffix}@enamel.test")
            membership = membership_factory(clinic, user, role=role)
        return Provider.all_objects.create(
            clinic=clinic, membership=membership, display_name=display_name, **extra
        )

    return _create


@pytest.fixture
def appointment_type_factory(db):
    from apps.scheduling.models import AppointmentType

    def _create(clinic, *, code=None, name="Checkup", duration_min=30, **extra):
        return AppointmentType.all_objects.create(
            clinic=clinic,
            code=code or f"type-{uuid.uuid4().hex[:6]}",
            name=name,
            duration_min=duration_min,
            **extra,
        )

    return _create


@pytest.fixture
def rule_factory(db):
    from datetime import time as time_type

    from apps.scheduling.models import AvailabilityRule

    def _create(clinic, location, *, kind=AvailabilityRule.Kind.WORKING, weekday=0,
                start_time=time_type(9, 0), end_time=time_type(17, 0), provider=None, **extra):
        return AvailabilityRule.all_objects.create(
            clinic=clinic, location=location, kind=kind, weekday=weekday,
            start_time=start_time, end_time=end_time, provider=provider, **extra,
        )

    return _create


@pytest.fixture
def ai_seed(owner, location_factory, provider_factory, appointment_type_factory, patient_factory):
    """A bookable clinic plus a patient for the AI tool/voice tests."""

    def _seed(*, duration_min=30, date_of_birth=None, **type_extra):
        clinic = owner.clinic
        location = location_factory(clinic, name="Main")
        provider = provider_factory(clinic, membership=owner.membership, display_name="Dr Owner")
        appointment_type = appointment_type_factory(clinic, duration_min=duration_min, **type_extra)
        patient = patient_factory(
            clinic,
            first_name="Ada",
            last_name="Lovelace",
            date_of_birth=date_of_birth or date(1990, 1, 1),
            phone_e164="+919876500000",
        )
        return {
            "clinic": clinic,
            "location": location,
            "provider": provider,
            "appointment_type": appointment_type,
            "patient": patient,
        }

    return _seed


def local_dt(y: int, m: int, d: int, hh: int, mm: int, tz=KOLKATA) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=tz)


@pytest.fixture
def ai_engine():
    from apps.ai.providers.mock import MockProvider
    from apps.ai.voice.engine import ConversationEngine

    return ConversationEngine(provider=MockProvider())


@pytest.fixture
def seed_call(ai_engine):
    def _seed(owner, patient, *, confirmed=True):
        call = ai_engine.start_call(
            clinic=owner.clinic, patient=patient, started_by=owner.user
        )
        if confirmed:
            call.identity_confirmed = True
            call.save(update_fields=["identity_confirmed", "updated_at"])
        return call

    return _seed
