"""Fixtures shared by the RCM tests (T134)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from django.utils import timezone

from apps.billing.models import Invoice
from apps.core.tenancy import with_clinic
from apps.patients.models import Patient
from apps.patients.services.numbering import allocate_patient_number
from apps.rcm.models import InsurancePolicy, Payer


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


@pytest.fixture(autouse=True)
def enable_rcm(settings):
    """Enable the feature flag for every RCM test by default."""

    settings.RCM_ENABLED = True
    return settings


@pytest.fixture
def staff_factory(user_factory, clinic_factory, membership_factory):
    def _create(clinic=None, role: str = "owner", slug: str | None = None, email: str | None = None, **extra):
        from rest_framework.test import APIClient

        suffix = uuid.uuid4().hex[:8]
        client = APIClient()
        user = user_factory(email or f"{role}-{suffix}@rcm.test")
        if clinic is None:
            clinic = clinic_factory(slug=slug or f"rcm-{suffix}", **extra)
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
def member_factory(user_factory, membership_factory):
    def _create(clinic, role: str = "receptionist", email: str | None = None):
        from rest_framework.test import APIClient

        suffix = uuid.uuid4().hex[:8]
        client = APIClient()
        user = user_factory(email or f"{role}-{suffix}@rcm.test")
        membership = membership_factory(clinic, user, role=role)
        client.force_login(user)
        return StaffClient(client, user, clinic, membership, role)

    return _create


@pytest.fixture
def patient_factory(db):
    def _create(clinic, *, first_name: str = "Ada", last_name: str = "Lovelace", **extra):
        with with_clinic(clinic):
            return Patient.all_objects.create(
                clinic=clinic,
                number=allocate_patient_number(clinic),
                first_name=first_name,
                last_name=last_name,
                **extra,
            )

    return _create


@pytest.fixture
def payer_factory(db):
    def _create(clinic, *, code: str = "MOCK-INS", name: str = "Mock Insurance", is_active: bool = True):
        return Payer.all_objects.create(
            clinic=clinic, name=name, code=code, is_active=is_active
        )

    return _create


@pytest.fixture
def policy_factory(db):
    def _create(
        clinic,
        patient,
        payer,
        *,
        member_id: str = "M0001",
        plan_name: str = "Mock PPO",
        valid_from: date = date(2020, 1, 1),
        valid_to: date | None = None,
        is_active: bool = True,
    ):
        return InsurancePolicy.all_objects.create(
            clinic=clinic,
            patient=patient,
            payer=payer,
            member_id=member_id,
            plan_name=plan_name,
            valid_from=valid_from,
            valid_to=valid_to,
            is_active=is_active,
        )

    return _create


@pytest.fixture
def invoice_factory(db):
    def _create(clinic, patient, *, total_minor: int = 5000, number: str | None = None):
        return Invoice.all_objects.create(
            clinic=clinic,
            patient=patient,
            number=number or f"INV-{uuid.uuid4().hex[:8].upper()}",
            status=Invoice.Status.ISSUED,
            currency=clinic.currency,
            subtotal_minor=total_minor,
            total_minor=total_minor,
            balance_minor=total_minor,
            issued_at=timezone.now(),
        )

    return _create
