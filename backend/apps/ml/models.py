"""ML registry and inference persistence (T131).

Three tables:

* ``NoShowModel`` -- the versioned registry. ``clinic`` is nullable: a row with
  no clinic is the global default model, a row with a clinic overrides it. Only
  one ``active`` row may exist per scope (the training script retires the
  previous one in the same transaction).
* ``TrainingRun`` -- one row per training execution, keeping the seed, the
  dataset hash and the full metrics document that produced a model.
* ``Prediction`` -- one row per served inference. ``model_version`` is a plain
  string, not a foreign key, so retiring a model never breaks the history: the
  predictions stay readable exactly as they were made.

There is no clinical-validity claim anywhere: every row carries
``is_synthetic`` and the API returns the disclosure flag.
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.core.tenancy import ClinicScopedModel
from ml.no_show.config import (
    DECISION_HIGH_RISK,
    DECISION_LOW_RISK,
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
    STATUS_RETIRED,
)


class NoShowModel(models.Model):
    """One versioned no-show model artifact plus its metrics."""

    class Status(models.TextChoices):
        CANDIDATE = STATUS_CANDIDATE, "Candidate"
        ACTIVE = STATUS_ACTIVE, "Active"
        RETIRED = STATUS_RETIRED, "Retired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    # Null clinic == the global default model; a clinic row overrides it.
    clinic = models.ForeignKey(
        "clinics.Clinic",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="no_show_models",
    )
    version = models.CharField(max_length=64, unique=True)
    algorithm = models.CharField(max_length=64, blank=True, default="")
    features = models.JSONField(default=list, blank=True)
    dataset_hash = models.CharField(max_length=64, blank=True, default="")
    threshold = models.FloatField(default=0.5)
    artifact_path = models.CharField(max_length=512, blank=True, default="")
    artifact_hash = models.CharField(max_length=64, blank=True, default="")
    metrics_json = models.JSONField(default=dict, blank=True)
    metrics_hash = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.CANDIDATE
    )
    is_synthetic = models.BooleanField(default=True)
    sklearn_version = models.CharField(max_length=32, blank=True, default="")
    trained_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ml_noshowmodel"
        ordering = ["-trained_at", "-id"]
        indexes = [
            models.Index(fields=["status"], name="noshow_model_status_idx"),
            models.Index(fields=["clinic", "status"], name="noshow_model_clinic_status_idx"),
        ]
        verbose_name = "No-show model"
        verbose_name_plural = "No-show models"

    def __str__(self) -> str:
        scope = "global" if self.clinic_id is None else str(self.clinic_id)
        return f"{self.version} ({self.status}, {scope})"


class TrainingRun(models.Model):
    """One execution of the training pipeline."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    model = models.ForeignKey(
        NoShowModel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="training_runs",
    )
    clinic = models.ForeignKey(
        "clinics.Clinic",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ml_training_runs",
    )
    seed = models.IntegerField()
    dataset_hash = models.CharField(max_length=64, blank=True, default="")
    params = models.JSONField(default=dict, blank=True)
    metrics_json = models.JSONField(default=dict, blank=True)
    metrics_hash = models.CharField(max_length=64, blank=True, default="")
    artifact_hash = models.CharField(max_length=64, blank=True, default="")
    git_sha = models.CharField(max_length=64, blank=True, default="unknown")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ml_trainingrun"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["dataset_hash"], name="training_run_dataset_idx"),
        ]
        verbose_name = "Training run"
        verbose_name_plural = "Training runs"

    def __str__(self) -> str:
        return f"run {self.seed} {self.dataset_hash[:12]}"


class Prediction(ClinicScopedModel):
    """One persisted inference result.

    ``model_version`` is a string so a retired (or deleted) model cannot orphan
    or hide the prediction: the row keeps the exact version, threshold and
    factors it was produced with.
    """

    class Decision(models.TextChoices):
        HIGH_RISK = DECISION_HIGH_RISK, "High risk"
        LOW_RISK = DECISION_LOW_RISK, "Low risk"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    appointment = models.ForeignKey(
        "scheduling.Appointment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="no_show_predictions",
    )
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="no_show_predictions",
    )
    model_version = models.CharField(max_length=64)
    probability = models.FloatField()
    threshold = models.FloatField()
    decision = models.CharField(max_length=16, choices=Decision.choices)
    factors_json = models.JSONField(default=list, blank=True)
    is_synthetic = models.BooleanField(default=True)

    class Meta:
        db_table = "ml_prediction"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["clinic", "patient"], name="prediction_clinic_patient_idx"
            ),
            models.Index(
                fields=["clinic", "model_version"], name="prediction_clinic_version_idx"
            ),
        ]
        verbose_name = "Prediction"
        verbose_name_plural = "Predictions"

    def __str__(self) -> str:
        return f"{self.model_version} {self.decision} ({self.probability:.3f})"


# Stable OpenAPI enum names (SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]).
NOSHOW_MODEL_STATUS_CHOICES = NoShowModel.Status.choices
PREDICTION_DECISION_CHOICES = Prediction.Decision.choices


__all__ = [
    "NOSHOW_MODEL_STATUS_CHOICES",
    "PREDICTION_DECISION_CHOICES",
    "NoShowModel",
    "Prediction",
    "TrainingRun",
]
