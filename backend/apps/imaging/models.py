"""Imaging archive models (T127).

Three tenant tables mirror the DICOM hierarchy:

* :class:`Study` -- one patient examination, keyed by ``study_instance_uid``.
* :class:`Series` -- one acquisition inside a study.
* :class:`Instance` -- one DICOM SOP instance (a single image/frame set). The
  stored bytes are content-addressed and carry a SHA-256 so a re-download can be
  verified byte for byte.

``InferenceJob`` (T129) lives in its own migration so the archive schema can be
reviewed independently of the assistive-analysis feature.

No row ever stores patient identifiers extracted from DICOM: the configured
de-identification tags are removed from the metadata before an instance is
persisted, and only the resulting tag list (``deid_json``) is kept.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.core.tenancy import ClinicScopedModel


class Study(ClinicScopedModel):
    """One imaging study for a patient."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    class Source(models.TextChoices):
        STAFF = "staff", "Staff"
        IMPORT = "import", "Import"
        DEVICE = "device", "Device"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="imaging_studies",
    )
    study_instance_uid = models.CharField(max_length=128, unique=True)
    accession = models.CharField(max_length=64, blank=True, default="")
    modality = models.CharField(max_length=16, blank=True, default="")
    study_date = models.DateField(null=True, blank=True)
    description = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.STAFF)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="imaging_studies",
    )

    class Meta:
        db_table = "imaging_study"
        ordering = ["-study_date", "-created_at", "id"]
        indexes = [
            models.Index(fields=["clinic", "patient"], name="img_study_clinic_patient_idx"),
            models.Index(fields=["clinic", "study_date"], name="img_study_clinic_date_idx"),
            models.Index(fields=["clinic", "modality"], name="img_study_clinic_modality_idx"),
        ]
        verbose_name = "Imaging study"
        verbose_name_plural = "Imaging studies"

    def __str__(self) -> str:
        return f"{self.study_instance_uid} ({self.modality or 'unknown'})"


class Series(ClinicScopedModel):
    """One series inside a study."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    study = models.ForeignKey(Study, on_delete=models.CASCADE, related_name="series")
    series_instance_uid = models.CharField(max_length=128, unique=True)
    modality = models.CharField(max_length=16, blank=True, default="")
    body_part = models.CharField(max_length=64, blank=True, default="")
    description = models.CharField(max_length=255, blank=True, default="")
    instance_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "imaging_series"
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["clinic", "study"], name="img_series_clinic_study_idx"),
        ]
        verbose_name = "Imaging series"
        verbose_name_plural = "Imaging series"

    def __str__(self) -> str:
        return f"{self.series_instance_uid} ({self.instance_count} instances)"


class Instance(ClinicScopedModel):
    """One stored DICOM SOP instance."""

    class PreviewStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    series = models.ForeignKey(Series, on_delete=models.CASCADE, related_name="instances")
    sop_instance_uid = models.CharField(max_length=128, unique=True)
    instance_number = models.PositiveIntegerField(null=True, blank=True)
    rows = models.PositiveIntegerField(null=True, blank=True)
    columns = models.PositiveIntegerField(null=True, blank=True)
    bits_allocated = models.PositiveSmallIntegerField(null=True, blank=True)
    photometric = models.CharField(max_length=32, blank=True, default="")
    transfer_syntax = models.CharField(max_length=64, blank=True, default="")
    storage_key = models.CharField(max_length=512)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField(default=0)
    #: Non-PHI de-identification record: removed tag names + the pixel-data hash.
    deid_json = models.JSONField(default=dict, blank=True)
    preview_key = models.CharField(max_length=512, null=True, blank=True)
    preview_status = models.CharField(
        max_length=16,
        choices=PreviewStatus.choices,
        default=PreviewStatus.PENDING,
    )

    class Meta:
        db_table = "imaging_instance"
        ordering = ["instance_number", "created_at", "id"]
        indexes = [
            models.Index(fields=["clinic", "series"], name="img_inst_clinic_series_idx"),
            models.Index(fields=["clinic", "sop_instance_uid"], name="img_inst_clinic_sop_idx"),
        ]
        verbose_name = "Imaging instance"
        verbose_name_plural = "Imaging instances"

    def __str__(self) -> str:
        return self.sop_instance_uid


class InferenceJob(ClinicScopedModel):
    """One assistive-analysis run over a study (T129).

    The row is the durable record of the run: the preprocessing + inference
    config that produced it (``config_json``), the findings (``findings_json``,
    always ``status=suggested``) and a stable ``output_hash`` over those findings
    so two identical runs are provably identical.

    ``findings_json`` stays ``None`` unless the run completed: a failed run never
    leaves partial findings behind.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    study = models.ForeignKey(Study, on_delete=models.CASCADE, related_name="inference_jobs")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    model_name = models.CharField(max_length=128, blank=True, default="")
    model_version = models.CharField(max_length=64, blank=True, default="")
    config_json = models.JSONField(default=dict, blank=True)
    findings_json = models.JSONField(null=True, blank=True)
    output_hash = models.CharField(max_length=64, blank=True, default="")
    error_code = models.CharField(max_length=64, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="imaging_inference_jobs",
    )

    class Meta:
        db_table = "imaging_inferencejob"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["clinic", "study"], name="img_job_clinic_study_idx"),
            models.Index(fields=["clinic", "status"], name="img_job_clinic_status_idx"),
        ]
        verbose_name = "Imaging inference job"
        verbose_name_plural = "Imaging inference jobs"

    def __str__(self) -> str:
        return f"{self.model_name}@{self.model_version} [{self.status}]"


# Stable OpenAPI enum names (SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]).
STUDY_STATUS_CHOICES = Study.Status.choices
STUDY_SOURCE_CHOICES = Study.Source.choices
INSTANCE_PREVIEW_STATUS_CHOICES = Instance.PreviewStatus.choices
INFERENCE_JOB_STATUS_CHOICES = InferenceJob.Status.choices


__all__ = [
    "INFERENCE_JOB_STATUS_CHOICES",
    "INSTANCE_PREVIEW_STATUS_CHOICES",
    "STUDY_SOURCE_CHOICES",
    "STUDY_STATUS_CHOICES",
    "InferenceJob",
    "Instance",
    "Series",
    "Study",
]
