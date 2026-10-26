"""Cross-clinic isolation and patient ownership (T127)."""

from __future__ import annotations

import pytest

from apps.core.errors import ApiError
from apps.imaging.models import Instance, Study
from apps.imaging.services import access

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_clinics(owner, staff_factory, upload_study, patient_factory):
    """Clinic A (owner) and clinic B (other) each with one uploaded study."""

    other = staff_factory(role="owner", slug="clinic-b-imaging")
    response, other_patient = upload_study(
        "study1_series1_instance1.dcm", client=other
    )
    assert response.status_code == 201

    study = Study.all_objects.get(clinic_id=other.clinic.id)
    instance = Instance.all_objects.get(clinic_id=other.clinic.id)
    return {
        "other": other,
        "study": study,
        "instance": instance,
        "other_patient": other_patient,
        "owner": owner,
    }


def test_foreign_study_detail_is_404(two_clinics):
    owner = two_clinics["owner"]
    response = owner.get(f"/api/v1/imaging/studies/{two_clinics['study'].id}/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_foreign_instance_preview_is_404(two_clinics):
    owner = two_clinics["owner"]
    response = owner.get(f"/api/v1/imaging/instances/{two_clinics['instance'].id}/preview/")
    assert response.status_code == 404


def test_foreign_wado_is_404(two_clinics):
    owner = two_clinics["owner"]
    sop = two_clinics["instance"].sop_instance_uid
    response = owner.get(f"/api/v1/imaging/dicomweb/instances/{sop}/")
    assert response.status_code == 404


def test_foreign_qido_series_is_404(two_clinics):
    owner = two_clinics["owner"]
    uid = two_clinics["study"].study_instance_uid
    response = owner.get(f"/api/v1/imaging/dicomweb/studies/{uid}/series/")
    assert response.status_code == 404


def test_foreign_study_inference_is_404(two_clinics):
    owner = two_clinics["owner"]
    response = owner.post(f"/api/v1/imaging/studies/{two_clinics['study'].id}/inference/", {})
    assert response.status_code == 404


def test_patient_can_only_reach_their_own_instance(owner, upload_study, patient_factory):
    response, patient = upload_study("study1_series1_instance1.dcm")
    assert response.status_code == 201
    instance = Instance.all_objects.get(clinic_id=owner.clinic.id)
    other_patient = patient_factory(owner.clinic, first_name="Other")

    # The owning patient reaches the instance; another patient gets a 404.
    assert access.instance_for_patient(patient, instance.id).id == instance.id
    with pytest.raises(ApiError) as excinfo:
        access.instance_for_patient(other_patient, instance.id)
    assert excinfo.value.status_code == 404

    assert access.study_for_patient(patient, instance.series.study_id).id == instance.series.study_id
    with pytest.raises(ApiError):
        access.study_for_patient(other_patient, instance.series.study_id)


def test_unauthenticated_study_list_is_401(api_client):
    response = api_client.get("/api/v1/imaging/studies/")
    assert response.status_code == 401
