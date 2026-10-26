"""Odontogram persistence models (T108).

Three ideas drive the schema:

1. **A tooth state is a layer, not a tooth.** ``(patient, tooth, condition)``
   is the identity, so a crown can coexist with caries on the same tooth and
   each layer keeps its own surfaces, status and version.
2. **Archive, never delete.** ``archived_at`` marks a layer as gone; the unique
   constraint is *partial* (``archived_at IS NULL``), so re-adding the same
   condition after an archive starts a fresh row and a fresh version chain.
3. **History is append-only.** ``ToothStateEvent`` refuses ``save()`` on an
   existing row and refuses ``delete()`` outright, so the only legal write is
   the insert performed by the service layer. ``before``/``after`` snapshots
   let the history endpoint replay to the current state.

``ConditionCode`` is global reference data and therefore does *not* extend
``ClinicScopedModel``.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.tenancy import ClinicScopedModel

from . import fdi, validators


class ChartHistoryImmutableError(RuntimeError):
    """Raised when code attempts to update or delete a chart history row.

    The history table is append-only: the only legal write is the insert made
    by ``services.history.record_event``. A DB-level trigger that makes this
    true for every writer lands with the T135 hardening pass; this exception is
    the application-level guard, mirroring ``core.errors.AuditImmutableError``.
    """


class ConditionCode(models.Model):
    """Global catalogue of tooth condition codes (seeded by data migration)."""

    code = models.CharField(max_length=64, primary_key=True)
    label = models.CharField(max_length=128)
    category = models.CharField(max_length=32)
    default_status = models.CharField(
        max_length=16,
        choices=validators.STATUS_CHOICES,
        default="existing",
    )
    surfaces_required = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "charting_conditioncode"
        ordering = ["sort_order", "code"]
        verbose_name = "Condition code"
        verbose_name_plural = "Condition codes"

    def __str__(self) -> str:
        return f"{self.code} ({self.label})"


class ToothState(ClinicScopedModel):
    """One condition layer on one tooth for one patient."""

    class Status(models.TextChoices):
        EXISTING = "existing", "Existing"
        PLANNED = "planned", "Planned"
        REFERRED = "referred", "Referred"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="tooth_states",
    )
    tooth = models.SmallIntegerField()
    dentition = models.CharField(
        max_length=16,
        choices=fdi.DENTITION_CHOICES,
        default="permanent",
    )
    # ``to_field`` keeps the column human-readable (``condition_code``) instead
    # of a meaningless surrogate integer, which makes the table self-describing
    # in psql and in exports.
    condition = models.ForeignKey(
        ConditionCode,
        to_field="code",
        db_column="condition_code",
        on_delete=models.PROTECT,
        related_name="tooth_states",
    )
    status = models.CharField(
        max_length=16,
        choices=validators.STATUS_CHOICES,
        default="existing",
    )
    # A plain JSON list, never a Postgres array: JSONField round-trips a Python
    # list identically and avoids the array-cast class of bug the spec calls out.
    surfaces = models.JSONField(default=list, blank=True)
    severity = models.SmallIntegerField(null=True, blank=True)
    material = models.CharField(max_length=64, blank=True, default="")
    note = models.TextField(blank=True, default="")
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    # Interim link: T117 (plans) adds the real FK. Named ``plan_item_id`` so the
    # column already matches the future FK attname and no data migration is
    # needed when the constraint is added.
    plan_item_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "charting_toothstate"
        ordering = ["tooth", "condition__sort_order", "id"]
        constraints = [
            # Partial unique: only one *active* layer per (patient, tooth,
            # condition). Archived rows fall outside the constraint, which is
            # what lets a condition be archived and re-added later.
            models.UniqueConstraint(
                fields=["patient", "tooth", "condition"],
                condition=models.Q(archived_at__isnull=True),
                name="uniq_active_tooth_state",
            ),
        ]
        indexes = [
            models.Index(
                fields=["clinic", "patient", "tooth"],
                name="ts_clinic_patient_tooth_idx",
            ),
        ]
        verbose_name = "Tooth state"
        verbose_name_plural = "Tooth states"

    def __str__(self) -> str:
        return f"tooth {self.tooth} {self.condition_id} ({self.status})"

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class ToothStateEvent(ClinicScopedModel):
    """Append-only odontogram history row."""

    class EventType(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        ARCHIVED = "archived", "Archived"
        TRANSITIONED = "transitioned", "Transitioned"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    # Nullable so a future migration that drops a state row does not silently
    # destroy the history that references it; PROTECT keeps the row alive.
    tooth_state = models.ForeignKey(
        ToothState,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="events",
    )
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="tooth_state_events",
    )
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    occurred_at = models.DateTimeField(default=timezone.now)
    version = models.PositiveIntegerField(default=1)
    idempotency_key = models.CharField(max_length=255, blank=True, default="")
    batch_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "charting_toothstateevent"
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(
                fields=["clinic", "patient", "-occurred_at"],
                name="tse_clinic_patient_at_idx",
            ),
            models.Index(
                fields=["tooth_state", "-occurred_at"],
                name="tse_state_at_idx",
            ),
        ]
        verbose_name = "Tooth state event"
        verbose_name_plural = "Tooth state events"

    def __str__(self) -> str:
        return f"{self.event_type} v{self.version} ({self.pk})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # ``_state.adding`` (not ``pk is None``): the primary key is a UUID with
        # a default, so a brand-new instance already carries a pk.
        if not self._state.adding:
            raise ChartHistoryImmutableError(
                "ToothStateEvent rows are append-only and cannot be updated."
            )
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ChartHistoryImmutableError(
            "ToothStateEvent rows are append-only and cannot be deleted."
        )


class ChartBatchLog(ClinicScopedModel):
    """Stored result of an idempotent batch save.

    A batch is retried by the client on any transport failure, so the whole
    response is stored keyed by ``(clinic, key)`` and replayed verbatim. The
    row carries the response payload only (ids, enum values, counts); notes and
    surfaces never leave the state tables.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="chart_batch_logs",
    )
    key = models.CharField(max_length=255)
    result = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "charting_chartbatchlog"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "key"],
                name="uniq_chart_batch_clinic_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["clinic", "patient"],
                name="cbl_clinic_patient_idx",
            ),
        ]
        verbose_name = "Chart batch log"
        verbose_name_plural = "Chart batch logs"

    def __str__(self) -> str:
        return f"batch {self.key} ({self.patient_id})"


# Stable OpenAPI enum names (SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]).
TOOTH_STATE_STATUS_CHOICES = ToothState.Status.choices
TOOTH_STATE_DENTITION_CHOICES = fdi.DENTITION_CHOICES
CHART_DENTITION_CHOICES = fdi.CHART_DENTITION_CHOICES
TOOTH_STATE_EVENT_TYPE_CHOICES = ToothStateEvent.EventType.choices


__all__ = [
    "CHART_DENTITION_CHOICES",
    "ChartBatchLog",
    "ChartHistoryImmutableError",
    "ConditionCode",
    "TOOTH_STATE_DENTITION_CHOICES",
    "TOOTH_STATE_EVENT_TYPE_CHOICES",
    "TOOTH_STATE_STATUS_CHOICES",
    "ToothState",
    "ToothStateEvent",
]
