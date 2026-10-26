"""Fixtures shared by the imaging tests (T127, T129)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.imaging.dicom.fixtures import build_fixtures


@pytest.fixture(scope="session")
def dicom_files() -> dict[str, bytes]:
    """Deterministic DICOM fixture bytes (built once per session)."""

    return build_fixtures()


@pytest.fixture
def dicom_file(dicom_files):
    """Return a ``SimpleUploadedFile`` for a named fixture."""

    def _create(name: str = "study1_series1_instance1.dcm") -> SimpleUploadedFile:
        return SimpleUploadedFile(name, dicom_files[name], content_type="application/dicom")

    return _create


class StaffClient:
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

    def post_multipart(self, url, data=None, **kwargs):
        kwargs.setdefault("format", "multipart")
        return self.client.post(url, data, **self._kwargs(kwargs))


@pytest.fixture
def staff_factory(user_factory, clinic_factory, membership_factory):
    def _create(clinic=None, role: str = "owner", slug: str | None = None, **extra):
        from rest_framework.test import APIClient

        suffix = uuid.uuid4().hex[:8]
        client = APIClient()
        user = user_factory(f"{role}-{suffix}@enamel.test")
        if clinic is None:
            clinic = clinic_factory(slug=slug or f"clinic-{suffix}", **extra)
        membership = membership_factory(clinic, user, role=role)
        client.force_login(user)
        return StaffClient(client, user, clinic, membership, role)

    return _create


@pytest.fixture
def owner(staff_factory):
    return staff_factory(role="owner")


@pytest.fixture
def patient_factory(db):
    from apps.core.tenancy import with_clinic
    from apps.patients.models import Patient
    from apps.patients.services.numbering import allocate_patient_number

    def _create(clinic, *, first_name="Ada", last_name="Lovelace", **extra):
        with with_clinic(clinic):
            return Patient.all_objects.create(
                clinic=clinic,
                number=allocate_patient_number(clinic),
                first_name=first_name,
                last_name=last_name,
                date_of_birth=extra.pop("date_of_birth", date(1985, 5, 1)),
                **extra,
            )

    return _create


@pytest.fixture
def upload_study(owner, patient_factory):
    """Upload one or more fixtures for a fresh patient; return the response."""

    def _upload(*names: str, client=None, patient=None, extra=None):
        active = client or owner
        target = patient or patient_factory(active.clinic)
        chosen = names or ("study1_series1_instance1.dcm",)
        data = {
            "patient": str(target.id),
            "files": [build_upload(name) for name in chosen],
        }
        if extra:
            data.update(extra)
        response = active.post_multipart("/api/v1/imaging/studies/", data)
        return response, target

    return _upload


def build_upload(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, build_fixtures()[name], content_type="application/dicom")


__all__ = ["StaffClient", "build_upload"]
