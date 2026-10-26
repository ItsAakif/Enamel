"""Assistive inference jobs over a study (T129).

The job lifecycle is ``queued -> running -> completed|failed`` and lives in an
:class:`~apps.imaging.models.InferenceJob` row. The durable ``core.JobRequest``
carries only the job id; the handler (``imaging.inference``) reloads the study
inside its clinic scope, runs the deterministic pipeline and persists the
findings.

Failure handling is deliberate: an expected pipeline failure marks the job
``failed`` with a stable ``error_code`` and leaves ``findings_json`` empty, so a
failed run can never leave partial findings behind.

Findings are advisory (``status=suggested``). Nothing here writes to the chart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.core.audit import record as record_audit
from apps.core.jobs import enqueue
from ml.cv.evaluate import LIMITATIONS
from ml.cv.infer import findings_to_json, output_hash, predict
from ml.cv.preprocess import load_config

from .. import storage
from ..dicom.parse import InvalidDicomError, parse_dicom
from ..dicom.pixels import PixelDecodeError, pixel_array
from ..models import InferenceJob, Instance

JOB_KIND = "imaging.inference"
JOB_QUEUE = "ml"
DEFAULT_CONFIG = "ml/cv/configs/fixture.yaml"


class InferenceError(RuntimeError):
    """A pipeline failure that must be recorded on the job (never retried blindly)."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


def cv_config_path() -> Path:
    raw = getattr(settings, "IMAGING_CV_CONFIG", DEFAULT_CONFIG)
    path = Path(raw)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    return path


def load_cv_config() -> dict[str, Any]:
    return load_config(cv_config_path())


def model_identity(config: dict[str, Any]) -> tuple[str, str]:
    model = config.get("model", {}) if isinstance(config, dict) else {}
    return str(model.get("name", "baseline")), str(model.get("version", "0"))


def _predict_study(study: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Run the deterministic pipeline over every instance in ``study``."""

    instances = list(
        Instance.all_objects.filter(series__study=study).order_by(
            "series_id", "instance_number", "created_at", "id"
        )
    )
    if not instances:
        raise InferenceError("no_instances", "The study has no instances to analyse.")

    results: list[dict[str, Any]] = []
    for instance in instances:
        try:
            dataset = parse_dicom(storage.read_bytes(instance.storage_key))
        except (InvalidDicomError, OSError) as exc:
            raise InferenceError("unreadable_instance", str(exc)) from exc
        try:
            array = pixel_array(dataset)
        except PixelDecodeError as exc:
            raise InferenceError("undecodable_instance", str(exc)) from exc

        findings = predict(array, {"instance_id": str(instance.id)}, config)
        results.append(
            {
                "instance_id": str(instance.id),
                "sop_instance_uid": instance.sop_instance_uid,
                "findings": findings_to_json(findings),
            }
        )

    name, version = model_identity(config)
    return {
        "model": {"name": name, "version": version},
        "status": "suggested",
        "disclosure": LIMITATIONS,
        "instances": results,
    }


def create_inference_job(study: Any, *, actor: Any = None, request: Any = None) -> InferenceJob:
    """Create the job row and dispatch its durable task."""

    config = load_cv_config()
    name, version = model_identity(config)
    job = InferenceJob.all_objects.create(
        clinic_id=study.clinic_id,
        study=study,
        status=InferenceJob.Status.QUEUED,
        model_name=name,
        model_version=version,
        config_json=config,
        created_by=actor,
    )
    record_audit(
        "imaging.inference_requested",
        entity=job,
        after={
            "study": str(study.id),
            "model_name": name,
            "model_version": version,
        },
        actor=actor,
        request=request,
    )
    enqueue(
        JOB_KIND,
        {"job_id": str(job.id)},
        clinic=study.clinic_id,
        idempotency_key=f"{JOB_KIND}:{job.id}",
    )
    return job


def run_inference_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Durable handler: run one inference job and persist its outcome."""

    job_id = payload.get("job_id")
    job = InferenceJob.all_objects.filter(pk=job_id).first()
    if job is None:
        return {"status": "missing", "job_id": str(job_id)}

    job.status = InferenceJob.Status.RUNNING
    job.started_at = timezone.now()
    job.error_code = ""
    job.save(update_fields=["status", "started_at", "error_code", "updated_at"])

    try:
        findings = _predict_study(job.study, job.config_json or {})
    except InferenceError as exc:
        job.status = InferenceJob.Status.FAILED
        job.error_code = exc.code
        job.findings_json = None
        job.output_hash = ""
        job.finished_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "error_code",
                "findings_json",
                "output_hash",
                "finished_at",
                "updated_at",
            ]
        )
        record_audit(
            "imaging.inference_failed",
            entity=job,
            after={"study": str(job.study_id), "error_code": exc.code},
        )
        return {"status": "failed", "error_code": exc.code, "job_id": str(job.id)}

    job.findings_json = findings
    job.output_hash = output_hash(
        findings,
        model_name=job.model_name,
        model_version=job.model_version,
        config=job.config_json or {},
    )
    job.status = InferenceJob.Status.COMPLETED
    job.finished_at = timezone.now()
    job.save(
        update_fields=[
            "status",
            "findings_json",
            "output_hash",
            "finished_at",
            "updated_at",
        ]
    )
    record_audit(
        "imaging.inference_completed",
        entity=job,
        after={
            "study": str(job.study_id),
            "output_hash": job.output_hash,
            "model_version": job.model_version,
        },
    )
    return {
        "status": "completed",
        "output_hash": job.output_hash,
        "job_id": str(job.id),
    }


__all__ = [
    "DEFAULT_CONFIG",
    "InferenceError",
    "JOB_KIND",
    "JOB_QUEUE",
    "create_inference_job",
    "cv_config_path",
    "load_cv_config",
    "model_identity",
    "run_inference_job",
]
