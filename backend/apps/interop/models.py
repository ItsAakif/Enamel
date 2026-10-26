"""Interoperability models (T130).

Four tenant tables carry the PMS/FHIR integration state:

* :class:`ConnectorConfig` -- one configured PMS connection (Open Dental today).
  Credentials are Fernet-encrypted into ``credentials_enc`` and are never
  serialized back to a client.
* :class:`SyncRun` -- the durable record of one pull/push run. ``cursor`` makes a
  run resumable and ``counts_json`` holds the accurate per-entity tallies plus
  the recorded transient errors that were retried.
* :class:`ExternalIdMap` -- the idempotency backbone: one row per
  ``(connector, entity_type, external_id)`` binding an external key to an
  internal UUID, so a second run never creates a duplicate.
* :class:`SyncConflict` -- an external id that resolves to a *different*
  internal row than the one already mapped. The conflict is queued for review and
  the existing mapping is never overwritten.

``mode=simulator`` is the default; ``mode=live`` additionally requires stored
credentials and ``CONNECTOR_ALLOW_LIVE=true`` (enforced by the connector factory,
never by the model).
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.core.tenancy import ClinicScopedModel


class ConnectorKind(models.TextChoices):
    OPEN_DENTAL = "open_dental", "Open Dental"


class ConnectorMode(models.TextChoices):
    SIMULATOR = "simulator", "Simulator"
    LIVE = "live", "Live"


class EntityType(models.TextChoices):
    PATIENT = "patient", "Patient"
    APPOINTMENT = "appointment", "Appointment"


class SyncStatus(models.TextChoices):
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    PARTIAL = "partial", "Partial"
    FAILED = "failed", "Failed"


class ConflictStatus(models.TextChoices):
    OPEN = "open", "Open"
    RESOLVED = "resolved", "Resolved"


class ConnectorConfig(ClinicScopedModel):
    """A configured PMS connector for one clinic."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    kind = models.CharField(
        max_length=32, choices=ConnectorKind.choices, default=ConnectorKind.OPEN_DENTAL
    )
    name = models.CharField(max_length=255)
    mode = models.CharField(
        max_length=16, choices=ConnectorMode.choices, default=ConnectorMode.SIMULATOR
    )
    # Only required for ``mode=live``; the simulator needs no network endpoint.
    base_url = models.CharField(max_length=512, blank=True, default="")
    # Fernet ciphertext of the credential blob. Never returned by any serializer.
    credentials_enc = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    # Simulator seed data and fault-injection plan (see connectors.simulator).
    settings_json = models.JSONField(default=dict, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "interop_connectorconfig"
        ordering = ["name", "id"]
        indexes = [
            models.Index(fields=["clinic", "is_active"], name="connector_clinic_active_idx"),
            models.Index(fields=["clinic", "kind"], name="connector_clinic_kind_idx"),
        ]
        verbose_name = "Connector config"
        verbose_name_plural = "Connector configs"

    def __str__(self) -> str:
        return f"{self.name} ({self.kind}/{self.mode})"

    @property
    def is_live(self) -> bool:
        return self.mode == ConnectorMode.LIVE


class SyncRun(ClinicScopedModel):
    """One connector synchronisation run (durable, resumable)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    connector = models.ForeignKey(
        ConnectorConfig,
        on_delete=models.CASCADE,
        related_name="runs",
    )
    direction = models.CharField(max_length=16, default="pull")
    status = models.CharField(
        max_length=16, choices=SyncStatus.choices, default=SyncStatus.RUNNING
    )
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    counts_json = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    # Opaque resume marker (ISO-8601 of the newest processed record).
    cursor = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "interop_syncrun"
        ordering = ["-started_at", "-id"]
        indexes = [
            models.Index(
                fields=["connector", "-started_at"], name="syncrun_connector_started_idx"
            ),
            models.Index(fields=["clinic", "status"], name="syncrun_clinic_status_idx"),
        ]
        verbose_name = "Sync run"
        verbose_name_plural = "Sync runs"

    def __str__(self) -> str:
        return f"run {self.status} ({self.connector_id})"


class ExternalIdMap(ClinicScopedModel):
    """Binds an external PMS id to one internal row (idempotency key)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    connector = models.ForeignKey(
        ConnectorConfig,
        on_delete=models.CASCADE,
        related_name="id_map",
    )
    entity_type = models.CharField(max_length=16, choices=EntityType.choices)
    external_id = models.CharField(max_length=128)
    internal_id = models.UUIDField()

    class Meta:
        db_table = "interop_externalidmap"
        ordering = ["entity_type", "external_id", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["connector", "entity_type", "external_id"],
                name="uniq_external_id_map",
            ),
        ]
        indexes = [
            models.Index(
                fields=["connector", "entity_type", "internal_id"],
                name="ext_id_map_internal_idx",
            ),
            models.Index(
                fields=["clinic", "entity_type"], name="ext_id_map_clinic_type_idx"
            ),
        ]
        verbose_name = "External id map"
        verbose_name_plural = "External id maps"

    def __str__(self) -> str:
        return f"{self.entity_type}:{self.external_id} -> {self.internal_id}"


class SyncConflict(ClinicScopedModel):
    """An external id whose declared internal identity differs from the mapping.

    The conflict is queued (``status=open``) for a human; the sync never
    overwrites the existing mapping or the conflicting internal row.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    connector = models.ForeignKey(
        ConnectorConfig,
        on_delete=models.CASCADE,
        related_name="conflicts",
    )
    entity_type = models.CharField(max_length=16, choices=EntityType.choices)
    external_id = models.CharField(max_length=128)
    internal_id = models.UUIDField(null=True, blank=True)
    reason = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16, choices=ConflictStatus.choices, default=ConflictStatus.OPEN
    )

    class Meta:
        db_table = "interop_syncconflict"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["connector", "status"], name="conflict_connector_status_idx"
            ),
            models.Index(
                fields=["connector", "entity_type", "external_id"],
                name="conflict_connector_ext_idx",
            ),
        ]
        verbose_name = "Sync conflict"
        verbose_name_plural = "Sync conflicts"

    def __str__(self) -> str:
        return f"conflict {self.external_id} ({self.reason})"


# Stable OpenAPI enum names (SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]).
CONNECTOR_KIND_CHOICES = ConnectorKind.choices
CONNECTOR_MODE_CHOICES = ConnectorMode.choices
ENTITY_TYPE_CHOICES = EntityType.choices
SYNC_STATUS_CHOICES = SyncStatus.choices
CONFLICT_STATUS_CHOICES = ConflictStatus.choices


__all__ = [
    "CONFLICT_STATUS_CHOICES",
    "CONNECTOR_KIND_CHOICES",
    "CONNECTOR_MODE_CHOICES",
    "ENTITY_TYPE_CHOICES",
    "SYNC_STATUS_CHOICES",
    "ConflictStatus",
    "ConnectorConfig",
    "ConnectorKind",
    "ConnectorMode",
    "EntityType",
    "ExternalIdMap",
    "SyncConflict",
    "SyncRun",
    "SyncStatus",
]
