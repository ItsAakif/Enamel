"""Fixtures shared by the portal tests (T121)."""

from __future__ import annotations

import uuid

import pytest

# The portal flow must run under the **portal role** in every suite run, so
# the patient-scoped policies are exercised and not only the application
# layer. The switch needs a connection that may ``SET ROLE``: in the ordinary
# run the test connection is the compose owner (a superuser, which may set any
# role) and in the test-fixture-role run (``enamel_test``, a member of no
# role) this fixture reconnects the connection with the owner's credentials.
# Without it the portal switch would be refused and the portal policies would
# stay unexercised in that run (F-10).
from apps.core.tests.rls_owner_connection import owner_connection  # noqa: F401
from apps.patients.models import Patient
from apps.patients.services.numbering import allocate_patient_number

PORTAL_PHONE = "+919876500001"


class StaffClient:
    """An authenticated staff client bound to a clinic and role."""

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


@pytest.fixture(autouse=True)
def reset_otp_outbox():
    from apps.portal.adapters.otp import reset_outbox

    reset_outbox()
    yield
    reset_outbox()


@pytest.fixture
def staff_factory(user_factory, clinic_factory, membership_factory):
    def _create(clinic=None, role: str = "owner", slug: str | None = None, email: str | None = None):
        from rest_framework.test import APIClient

        suffix = uuid.uuid4().hex[:8]
        client = APIClient()
        user = user_factory(email or f"{role}-{suffix}@portal.test")
        if clinic is None:
            clinic = clinic_factory(slug=slug or f"portal-clinic-{suffix}")
        membership = membership_factory(clinic, user, role=role)
        client.force_login(user)
        return StaffClient(client, user, clinic, membership, role)

    return _create


@pytest.fixture
def owner(staff_factory):
    return staff_factory(role="owner")


@pytest.fixture
def patient_factory(db):
    counter = {"n": 0}

    def _create(clinic, *, first_name: str = "Ada", last_name: str = "Lovelace", **extra):
        counter["n"] += 1
        extra.setdefault("phone_e164", f"+9198765000{counter['n']:02d}")
        return Patient.all_objects.create(
            clinic=clinic,
            number=allocate_patient_number(clinic),
            first_name=first_name,
            last_name=last_name,
            **extra,
        )

    return _create


@pytest.fixture
def portal_login(db):
    """Return an APIClient holding a verified ``enamel_portal`` session."""

    def _login(clinic, patient):
        from rest_framework.test import APIClient

        from apps.portal.adapters.otp import OUTBOX

        client = APIClient()
        contact = patient.phone_e164 or patient.email
        requested = client.post(
            "/api/v1/portal/auth/request-code/",
            {"clinic_slug": clinic.slug, "contact": contact},
            format="json",
        )
        assert requested.status_code == 202, requested.content
        challenge_id = requested.json()["challenge_id"]
        code = OUTBOX[-1]["code"]
        verified = client.post(
            "/api/v1/portal/auth/verify/",
            {"challenge_id": challenge_id, "code": code},
            format="json",
        )
        assert verified.status_code == 200, verified.content
        return client

    return _login


@pytest.fixture
def scheduling_deps(db):
    def _create(clinic, user, *, provider_name: str = "Dr Ada"):
        from apps.clinics.models import Location, Membership, Provider
        from apps.scheduling.models import AppointmentType

        location = Location.all_objects.create(clinic=clinic, name="Main location")
        membership = Membership.all_objects.get(clinic=clinic, user=user)
        provider = Provider.all_objects.create(
            clinic=clinic, membership=membership, display_name=provider_name
        )
        appointment_type = AppointmentType.all_objects.create(
            clinic=clinic, code="exam", name="Exam", duration_min=30
        )
        return {
            "location": location,
            "provider": provider,
            "appointment_type": appointment_type,
        }

    return _create


@pytest.fixture
def appointment_factory(db):
    from datetime import timedelta

    from django.utils import timezone

    from apps.scheduling.models import Appointment

    def _create(clinic, patient, deps, *, hours_ahead: int = 48, status: str = "scheduled"):
        start = timezone.now() + timedelta(hours=hours_ahead)
        return Appointment.all_objects.create(
            clinic=clinic,
            location=deps["location"],
            patient=patient,
            provider=deps["provider"],
            appointment_type=deps["appointment_type"],
            start_at=start,
            end_at=start + timedelta(minutes=30),
            status=status,
        )

    return _create


@pytest.fixture
def plan_factory(db):
    from apps.plans.services import plans as plan_service

    def _create(clinic, patient, *, title: str = "Plan", items=None):
        return plan_service.create_plan(
            clinic,
            patient=patient,
            title=title,
            items=items
            or [
                {
                    "procedure_code": "D1110",
                    "description": "Cleaning",
                    "quantity": 1,
                    "unit_price_minor": 5000,
                }
            ],
        )

    return _create


@pytest.fixture
def invoice_factory(db):
    from apps.billing.models import Invoice

    def _create(clinic, patient, *, number: str = "INV-000001", **extra):
        return Invoice.all_objects.create(
            clinic=clinic,
            patient=patient,
            number=number,
            status="issued",
            currency="INR",
            subtotal_minor=5000,
            total_minor=5000,
            balance_minor=5000,
            **extra,
        )

    return _create


@pytest.fixture
def document_factory(db):
    from apps.plans.services import documents as document_service

    def _create(clinic, patient, *, filename: str = "scan.pdf"):
        return document_service.upload(
            clinic=clinic,
            patient=patient,
            kind="xray",
            filename=filename,
            content_type="application/pdf",
            data=b"%PDF-1.4 portal test",
        )

    return _create
