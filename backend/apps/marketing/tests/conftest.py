"""Fixtures shared by the marketing tests (T132)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.tenancy import with_clinic
from apps.messaging.models import MessageTemplate
from apps.patients.models import Encounter, Patient
from apps.patients.services.numbering import allocate_patient_number

#: Quiet hours disabled (start == end) so a test send is never deferred.
QUIET_OFF = {"messaging": {"quiet_hours_start": 0, "quiet_hours_end": 0}}


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
        quiet_off: bool = True,
        **clinic_extra,
    ):
        from rest_framework.test import APIClient

        suffix = uuid.uuid4().hex[:8]
        client = APIClient()
        user = user_factory(email or f"{role}-{suffix}@marketing.test")
        if clinic is None:
            if quiet_off:
                clinic_extra.setdefault("settings_json", QUIET_OFF)
            clinic = clinic_factory(slug=slug or f"mkt-{suffix}", **clinic_extra)
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
def assistant(staff_factory):
    return staff_factory(role="assistant")


@pytest.fixture
def approver(staff_factory):
    """A second user holding ``marketing.approve`` (but not ``manage``)."""

    return staff_factory(role="receptionist")


@pytest.fixture
def member_factory(user_factory, membership_factory):
    """Add a second staff user (with a role) to an existing clinic."""

    def _create(clinic, role: str = "receptionist", email: str | None = None):
        from rest_framework.test import APIClient

        suffix = uuid.uuid4().hex[:8]
        client = APIClient()
        user = user_factory(email or f"{role}-{suffix}@marketing.test")
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
def visit_factory(db):
    def _create(patient, *, days_ago: int = 200):
        with with_clinic(patient.clinic):
            return Encounter.all_objects.create(
                clinic=patient.clinic,
                patient=patient,
                status=Encounter.Status.FINALIZED,
                occurred_at=timezone.now() - timedelta(days=days_ago),
            )

    return _create


@pytest.fixture
def template_factory(db):
    def _create(clinic, *, key: str = "spring_promo", channel: str = "email", **extra):
        return MessageTemplate.all_objects.create(
            clinic=clinic,
            key=key,
            name=extra.pop("name", "Spring promo"),
            channel=channel,
            purpose="marketing",
            subject=extra.pop("subject", "A note from us"),
            body=extra.pop("body", "Hi {first_name}, from {clinic}."),
            variables=["first_name", "clinic"],
            approval_status=MessageTemplate.ApprovalStatus.APPROVED,
            is_active=True,
            **extra,
        )

    return _create


@pytest.fixture
def grant_marketing_consent(db):
    from apps.messaging.services import consent as consent_service

    def _grant(patient, *, channel: str = "email"):
        return consent_service.grant(patient, channel, "marketing")

    return _grant
