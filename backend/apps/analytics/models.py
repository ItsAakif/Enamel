"""Analytics persistence: the durable export job (T133).

ARCHITECTURE.md says the analytics app has "no models except snapshots". An
export *is* a snapshot: the generated rows are materialised once, at request
time, and stored on the job so the signed download link serves exactly what was
authorised (a later data change cannot silently rewrite an export that has
already been handed out).

The row cap and the audit event are enforced by ``services.export``; the model
only stores the result.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.core.tenancy import ClinicScopedModel


class ExportJob(ClinicScopedModel):
    """One materialised report export."""

    class Report(models.TextChoices):
        OVERVIEW = "overview", "Overview"
        APPOINTMENTS = "appointments", "Appointments"
        REVENUE = "revenue", "Revenue"
        RECALL = "recall", "Recall"
        WAITLIST = "waitlist", "Waitlist"

    class Format(models.TextChoices):
        CSV = "csv", "CSV"
        JSON = "json", "JSON"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    report = models.CharField(max_length=32, choices=Report.choices)
    format = models.CharField(max_length=8, choices=Format.choices)
    date_from = models.DateField()
    date_to = models.DateField()
    location = models.ForeignKey(
        "clinics.Location",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analytics_exports",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    #: Human-facing reference, unique inside the clinic (``EXP-XXXXXXXX``).
    reference = models.CharField(max_length=32)
    #: The materialised rows (a list of flat dicts), capped at
    #: ``ANALYTICS_EXPORT_ROW_CAP``.
    payload_json = models.JSONField(default=list, blank=True)
    columns_json = models.JSONField(default=list, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    truncated = models.BooleanField(default=False)
    error = models.CharField(max_length=255, blank=True, default="")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "analytics_exportjob"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "reference"],
                name="uniq_export_clinic_reference",
            ),
        ]
        indexes = [
            models.Index(fields=["clinic", "status"], name="export_clinic_status_idx"),
            models.Index(
                fields=["clinic", "created_at"], name="export_clinic_created_idx"
            ),
        ]
        verbose_name = "Export job"
        verbose_name_plural = "Export jobs"

    def __str__(self) -> str:
        return f"{self.reference} {self.report}.{self.format} ({self.status})"


# Stable OpenAPI enum names (SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]).
EXPORT_REPORT_CHOICES = ExportJob.Report.choices
EXPORT_FORMAT_CHOICES = ExportJob.Format.choices
EXPORT_STATUS_CHOICES = ExportJob.Status.choices


__all__ = [
    "EXPORT_FORMAT_CHOICES",
    "EXPORT_REPORT_CHOICES",
    "EXPORT_STATUS_CHOICES",
    "ExportJob",
]
