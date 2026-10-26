"""Fixtures shared by the charting tests."""

from __future__ import annotations

import uuid

import pytest

from apps.charting import fdi
from apps.charting.models import ConditionCode, ToothState
from apps.core.tenancy import with_clinic
from apps.patients.models import Patient
from apps.patients.services.numbering import allocate_patient_number


@pytest.fixture(autouse=True)
def ensure_condition_catalogue(db):
    """Keep the global condition catalogue present for every test.

    ``@pytest.mark.django_db(transaction=True)`` flushes every table after the
    test, including ``charting_conditioncode``, whose rows come from the data
    migration rather than a fixture. Re-seeding when the table is empty makes
    the catalogue invariant hold for the whole suite.
    """

    from apps.charting.conditions import apply_condition_seeds

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

    def delete(self, url, **kwargs):
        return self.client.delete(url, **self._kwargs(kwargs))


@pytest.fixture
def staff_factory(user_factory, clinic_factory, membership_factory):
    def _create(clinic=None, role: str = "owner", slug: str | None = None, email: str | None = None):
        from rest_framework.test import APIClient

        suffix = uuid.uuid4().hex[:8]
        client = APIClient()
        user = user_factory(email or f"{role}-{suffix}@bright.test")
        if clinic is None:
            clinic = clinic_factory(slug=slug or f"chart-clinic-{suffix}")
        membership = membership_factory(clinic, user, role=role)
        client.force_login(user)
        return StaffClient(client, user, clinic, membership, role)

    return _create


@pytest.fixture
def owner(staff_factory):
    return staff_factory(role="owner")


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


@pytest.fixture
def chart_state_factory(db):
    """Create a ToothState row directly, bypassing the API."""

    def _create(
        patient,
        *,
        tooth: int = 11,
        condition_code: str = "caries",
        status: str = "existing",
        surfaces: list[str] | None = None,
        **extra,
    ) -> ToothState:
        info = fdi.tooth_info(tooth)
        return ToothState.all_objects.create(
            clinic=patient.clinic,
            patient=patient,
            tooth=tooth,
            dentition=info["dentition"],
            condition_id=condition_code,
            status=status,
            surfaces=surfaces if surfaces is not None else [],
            **extra,
        )

    return _create


@pytest.fixture
def conditions(db):
    """The seeded catalogue, keyed by code."""

    return {row.code: row for row in ConditionCode.objects.all()}


@pytest.fixture
def chart_urls():
    """Small helpers so tests do not hand-format URLs."""

    class _Urls:
        @staticmethod
        def chart(patient_id) -> str:
            return f"/api/v1/patients/{patient_id}/chart/"

        @staticmethod
        def batch(patient_id) -> str:
            return f"/api/v1/patients/{patient_id}/chart/batch/"

        @staticmethod
        def history(patient_id) -> str:
            return f"/api/v1/patients/{patient_id}/chart/history/"

        @staticmethod
        def transition(state_id) -> str:
            return f"/api/v1/chart/states/{state_id}/transition/"

    return _Urls()


@pytest.fixture
def save_batch():
    """POST a batch and return the response."""

    def _save(client, patient, changes, *, key: str = "batch-1", **extra):
        body = {"changes": changes, "idempotency_key": key, **extra}
        return client.post(f"/api/v1/patients/{patient.id}/chart/batch/", body)

    return _save


@pytest.fixture
def scope():
    """Run a block under an explicit clinic scope (for direct model writes)."""

    return with_clinic
