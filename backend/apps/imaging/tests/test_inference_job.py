"""Inference job lifecycle, determinism, failure and audit (T129)."""

from __future__ import annotations

import pytest

from apps.core.jobs import run_job
from apps.core.models import AuditEvent, JobRequest
from apps.imaging.models import InferenceJob, Instance, Study

pytestmark = pytest.mark.django_db


def _run_latest_job() -> None:
    """Run the most recent durable ``imaging.inference`` job row."""

    job_row = (
        JobRequest.objects.filter(kind="imaging.inference").order_by("-created_at").first()
    )
    assert job_row is not None
    run_job(job_row.id)


def _request_inference(owner, study) -> InferenceJob:
    response = owner.post(f"/api/v1/imaging/studies/{study.id}/inference/", {})
    assert response.status_code == 202, response.json()
    return InferenceJob.all_objects.get(pk=response.json()["job_id"])


def test_inference_request_creates_a_queued_job_and_records_config(owner, upload_study):
    response, _ = upload_study("study1_series1_instance1.dcm")
    assert response.status_code == 201
    study = Study.all_objects.get(clinic_id=owner.clinic.id)

    job = _request_inference(owner, study)

    assert job.status == InferenceJob.Status.QUEUED
    assert job.config_json["preprocess"]["resize"] == [64, 64]
    assert job.model_name == "baseline-intensity-threshold"
    assert job.model_version == "1.0.0"
    assert job.created_by_id == owner.user.id


def test_inference_job_runs_to_completion_and_persists_findings(owner, upload_study):
    response, _ = upload_study("study1_series1_instance1.dcm")
    assert response.status_code == 201
    study = Study.all_objects.get(clinic_id=owner.clinic.id)
    job = _request_inference(owner, study)

    _run_latest_job()

    job.refresh_from_db()
    assert job.status == InferenceJob.Status.COMPLETED
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.output_hash
    assert job.findings_json is not None
    assert job.findings_json["status"] == "suggested"
    assert job.findings_json["instances"]
    for entry in job.findings_json["instances"]:
        for finding in entry["findings"]:
            assert finding["status"] == "suggested"


def test_two_runs_have_identical_findings_and_output_hash(owner, upload_study):
    response, _ = upload_study("study1_series1_instance1.dcm")
    assert response.status_code == 201
    study = Study.all_objects.get(clinic_id=owner.clinic.id)

    first_job = _request_inference(owner, study)
    _run_latest_job()
    first_job.refresh_from_db()

    second_job = _request_inference(owner, study)
    _run_latest_job()
    second_job.refresh_from_db()

    assert first_job.findings_json == second_job.findings_json
    assert first_job.output_hash == second_job.output_hash
    assert first_job.output_hash


def test_failure_leaves_no_partial_findings(owner):
    # A study with no instances cannot be analysed.
    from apps.patients.models import Patient

    patient = Patient.all_objects.create(
        clinic=owner.clinic,
        number="EN-000900",
        first_name="Empty",
        last_name="Study",
    )
    study = Study.all_objects.create(
        clinic_id=owner.clinic.id,
        patient=patient,
        study_instance_uid="1.2.826.0.1.3680043.10.999.77",
        modality="IO",
    )
    job = _request_inference(owner, study)

    _run_latest_job()

    job.refresh_from_db()
    assert job.status == InferenceJob.Status.FAILED
    assert job.error_code == "no_instances"
    assert job.findings_json is None
    assert job.output_hash == ""
    assert job.finished_at is not None


def test_inference_history_lists_status_and_version(owner, upload_study):
    response, _ = upload_study("study1_series1_instance1.dcm")
    assert response.status_code == 201
    study = Study.all_objects.get(clinic_id=owner.clinic.id)
    job = _request_inference(owner, study)
    _run_latest_job()

    history = owner.get(f"/api/v1/imaging/studies/{study.id}/inference/")
    assert history.status_code == 200
    rows = history.json()["results"]
    assert len(rows) == 1
    assert rows[0]["id"] == str(job.id)
    assert rows[0]["status"] == "completed"
    assert rows[0]["model_version"] == "1.0.0"
    assert rows[0]["config_json"]["inference"]["threshold"] == 0.5


def test_inference_writes_audit_rows(owner, upload_study):
    response, _ = upload_study("study1_series1_instance1.dcm")
    assert response.status_code == 201
    study = Study.all_objects.get(clinic_id=owner.clinic.id)
    _request_inference(owner, study)
    _run_latest_job()

    requested = AuditEvent.objects.filter(
        clinic_id=owner.clinic.id, action="imaging.inference_requested"
    )
    completed = AuditEvent.objects.filter(
        clinic_id=owner.clinic.id, action="imaging.inference_completed"
    )
    assert requested.count() == 1
    assert completed.count() == 1
    assert requested.first().actor_id == owner.user.id
    assert completed.first().after["output_hash"]


def test_inference_does_not_write_to_the_chart(owner, upload_study):
    """Findings are advisory: no tooth state is created by a run."""

    from apps.charting.models import ToothState

    response, patient = upload_study("study1_series1_instance1.dcm")
    assert response.status_code == 201
    study = Study.all_objects.get(clinic_id=owner.clinic.id)
    _request_inference(owner, study)
    _run_latest_job()

    assert Instance.all_objects.filter(clinic_id=owner.clinic.id).count() == 1
    assert ToothState.all_objects.filter(clinic_id=owner.clinic.id).count() == 0
