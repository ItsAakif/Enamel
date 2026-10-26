"""Fixtures shared by the plans tests (T117)."""

from __future__ import annotations

import uuid

import pytest

from apps.core.tenancy import with_clinic
from apps.patients.models import Patient
from apps.patients.services.numbering import allocate_patient_number


@pytest.fixture(autouse=True)
def ensure_condition_catalogue(db):
    """Keep the global condition catalogue present for every test."""

    from apps.charting.conditions import apply_condition_seeds
    from apps.charting.models import ConditionCode

    if not ConditionCode.objects.exists():
        apply_condition_seeds(ConditionCode)


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
        user = user_factory(email or f"{role}-{suffix}@plans.test")
        if clinic is None:
            clinic = clinic_factory(slug=slug or f"plans-clinic-{suffix}")
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
def consent_templates(db):
    from apps.plans.seed import ensure_default_consent_templates

    def _ensure(clinic):
        return ensure_default_consent_templates(clinic)

    return _ensure


@pytest.fixture
def tooth_state_factory(db):
    from apps.charting import fdi
    from apps.charting.models import ToothState

    def _create(patient, *, tooth: int = 16, condition_code: str = "caries", status: str = "planned"):
        info = fdi.tooth_info(tooth)
        return ToothState.all_objects.create(
            clinic=patient.clinic,
            patient=patient,
            tooth=tooth,
            dentition=info["dentition"],
            condition_id=condition_code,
            status=status,
            surfaces=[],
        )

    return _create


@pytest.fixture
def plan_factory(db):
    from apps.plans.services import plans as plan_service

    def _create(clinic, patient, *, title: str = "Plan", items=None, actor=None):
        with with_clinic(clinic):
            return plan_service.create_plan(
                clinic,
                patient=patient,
                title=title,
                items=items or [],
                actor=actor,
            )

    return _create


@pytest.fixture
def scope():
    return with_clinic
