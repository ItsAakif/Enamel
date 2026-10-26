"""Fixtures shared by the ML tests (T131).

The trained bundle is session-scoped and database-free: the pipeline is a pure
function, so training once and reusing the artifact keeps the suite fast. The
registry rows are created per test against that artifact.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from ml.no_show.config import artifact_filename
from ml.no_show.registry import write_artifact


@pytest.fixture(scope="session")
def trained_bundle(tmp_path_factory):
    """Train one small-but-real model and persist its artifact to a temp dir."""

    from ml.no_show.train import train

    result = train(seed=42, dataset_size=1200)
    out_dir = tmp_path_factory.mktemp("noshow-artifacts")
    artifact_path = out_dir / artifact_filename(result["metrics"]["version"])
    write_artifact(result["artifact"], artifact_path)
    return {
        "result": result,
        "metrics": result["metrics"],
        "artifact_path": str(artifact_path),
    }


@pytest.fixture
def noshow_model(db, trained_bundle):
    """An active global registry row pointing at the trained artifact."""

    from apps.ml.models import NoShowModel, TrainingRun

    metrics = trained_bundle["metrics"]
    model = NoShowModel.objects.create(
        clinic=None,
        version=metrics["version"],
        algorithm=metrics["algorithm"],
        features=metrics["feature_names"],
        dataset_hash=metrics["dataset"]["hash"],
        threshold=metrics["threshold"],
        artifact_path=trained_bundle["artifact_path"],
        artifact_hash="test-artifact-hash",
        metrics_json=metrics,
        metrics_hash=metrics["metrics_hash"],
        status=NoShowModel.Status.ACTIVE,
        is_synthetic=True,
        sklearn_version=metrics["sklearn_version"],
    )
    TrainingRun.objects.create(
        model=model,
        seed=metrics["seed"],
        dataset_hash=metrics["dataset"]["hash"],
        metrics_json=metrics,
        metrics_hash=metrics["metrics_hash"],
        artifact_hash="test-artifact-hash",
        git_sha="unknown",
    )
    return model


# --------------------------------------------------------------------------- #
# Staff client (mirrors the other apps' test helper)
# --------------------------------------------------------------------------- #


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
def location_factory(db):
    from apps.clinics.models import Location

    def _create(clinic, *, name: str = "Main", **extra):
        return Location.all_objects.create(clinic=clinic, name=name, **extra)

    return _create


@pytest.fixture
def provider_factory(db, user_factory, membership_factory):
    from apps.clinics.models import Provider

    def _create(clinic, *, display_name: str = "Dr Test", membership=None, **extra):
        if membership is None:
            suffix = uuid.uuid4().hex[:8]
            user = user_factory(f"prov-{suffix}@enamel.test")
            membership = membership_factory(clinic, user, role="dentist")
        return Provider.all_objects.create(
            clinic=clinic, membership=membership, display_name=display_name, **extra
        )

    return _create


@pytest.fixture
def appointment_type_factory(db):
    from apps.scheduling.models import AppointmentType

    def _create(clinic, *, code: str = "checkup", name: str = "Checkup", **extra):
        return AppointmentType.all_objects.create(
            clinic=clinic, code=code, name=name, duration_min=30, **extra
        )

    return _create


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
                date_of_birth=extra.pop("date_of_birth", datetime(1985, 5, 1).date()),
                **extra,
            )

    return _create


@pytest.fixture
def seed_ml(owner, location_factory, provider_factory, appointment_type_factory, patient_factory):
    """A minimal clinic with one location, provider, type and patient."""

    def _seed(**type_extra):
        clinic = owner.clinic
        location = location_factory(clinic)
        provider = provider_factory(clinic, membership=owner.membership)
        appointment_type = appointment_type_factory(clinic, **type_extra)
        patient = patient_factory(clinic)
        return {
            "clinic": clinic,
            "location": location,
            "provider": provider,
            "appointment_type": appointment_type,
            "patient": patient,
        }

    return _seed


@pytest.fixture
def make_appointment(db):
    """Create an appointment directly (bypassing the service) for setup."""

    from apps.scheduling.models import Appointment

    def _create(seed, start, *, end=None, status="scheduled", **extra):
        appointment_type = extra.pop("appointment_type", seed["appointment_type"])
        if end is None:
            end = start + timedelta(minutes=appointment_type.duration_min)
        return Appointment.all_objects.create(
            clinic=extra.pop("clinic", seed["clinic"]),
            location=extra.pop("location", seed["location"]),
            patient=extra.pop("patient", seed["patient"]),
            provider=extra.pop("provider", seed["provider"]),
            appointment_type=appointment_type,
            start_at=start,
            end_at=end,
            status=status,
            **extra,
        )

    return _create


def utc(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    """A UTC instant for a clinic-local (Asia/Kolkata) wall-clock time."""

    return datetime(year, month, day, hour, minute, tzinfo=UTC)
