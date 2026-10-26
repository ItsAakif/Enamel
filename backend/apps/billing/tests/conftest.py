"""Fixtures shared by the billing tests (T119)."""

from __future__ import annotations

import uuid

import pytest

from apps.core.tenancy import with_clinic
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
        user = user_factory(email or f"{role}-{suffix}@billing.test")
        if clinic is None:
            clinic = clinic_factory(slug=slug or f"billing-clinic-{suffix}")
        membership = membership_factory(clinic, user, role=role)
        client.force_login(user)
        return StaffClient(client, user, clinic, membership, role)

    return _create


@pytest.fixture
def owner(staff_factory):
    return staff_factory(role="owner")


@pytest.fixture
def receptionist(staff_factory):
    return staff_factory(role="receptionist")


@pytest.fixture
def dentist(staff_factory):
    return staff_factory(role="dentist")


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
def invoice_factory(db):
    from apps.billing.services import invoices as invoice_service

    def _create(clinic, patient, *, lines=None, currency=None, actor=None, **extra):
        default_lines = [
            {
                "description": "Cleaning",
                "procedure_code": "D1110",
                "quantity": 1,
                "unit_price_minor": 100_000,
                "discount_minor": 0,
                "tax_rate_bp": 1800,
            }
        ]
        with with_clinic(clinic):
            return invoice_service.create_invoice(
                clinic,
                patient=patient,
                lines=default_lines if lines is None else lines,
                currency=currency,
                actor=actor,
                **extra,
            )

    return _create


@pytest.fixture
def plan_factory(db):
    from apps.plans.services import plans as plan_service

    def _create(clinic, patient, *, title="Plan", items=None, actor=None):
        with with_clinic(clinic):
            return plan_service.create_plan(
                clinic, patient=patient, title=title, items=items or [], actor=actor
            )

    return _create


@pytest.fixture
def accepted_plan(db):
    """Create an accepted plan with one accepted and one declined item."""

    from apps.plans.models import PlanItem
    from apps.plans.services import plans as plan_service

    def _create(clinic, patient, *, actor=None):
        with with_clinic(clinic):
            plan = plan_service.create_plan(
                clinic,
                patient=patient,
                title="Accepted plan",
                items=[
                    {
                        "procedure_code": "D2740",
                        "description": "Crown",
                        "tooth": 16,
                        "quantity": 1,
                        "unit_price_minor": 250_000,
                        "discount_minor": 0,
                        "tax_rate_bp": 1800,
                        "status": PlanItem.Status.ACCEPTED,
                    },
                    {
                        "procedure_code": "D2391",
                        "description": "Declined filling",
                        "tooth": 17,
                        "quantity": 1,
                        "unit_price_minor": 90_000,
                        "discount_minor": 0,
                        "tax_rate_bp": 1800,
                        "status": PlanItem.Status.DECLINED,
                    },
                ],
                actor=actor,
            )
            plan_service.transition(plan, "present", actor=actor)
            plan_service.transition(plan, "accept", actor=actor)
        return plan

    return _create


@pytest.fixture
def scope():
    return with_clinic
