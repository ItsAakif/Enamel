"""Shared cross-domain core models.

``IdempotencyRecord`` backs the ``Idempotency-Key`` contract for unsafe creates
(T102). Records are scoped by ``(user, clinic, key)`` and expire after 24 hours.
No request body is stored: only its hash and the response that was already
returned to the same user.

``AuditEvent`` is the append-only, per-clinic hash-chained audit log (T103).
Rows are inserted by ``core.audit.record`` inside the caller's transaction; the
model itself refuses any update or delete so no other code path can rewrite
history. Raw IPs and PII never reach the row: ``core.redaction`` masks
sensitive keys and only a salted IP hash is stored.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone

from .errors import AuditImmutableError

IDEMPOTENCY_TTL = timedelta(hours=24)


def idempotency_expiry() -> datetime:
    return timezone.now() + IDEMPOTENCY_TTL


class IdempotencyRecord(models.Model):
    """Stored result of an idempotent unsafe request."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="idempotency_records",
    )
    clinic = models.ForeignKey(
        "clinics.Clinic",
        on_delete=models.CASCADE,
        related_name="idempotency_records",
        null=True,
        blank=True,
    )
    key = models.CharField(max_length=255)
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=512)
    request_hash = models.CharField(max_length=64)
    status_code = models.PositiveSmallIntegerField()
    response_body = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=idempotency_expiry)

    class Meta:
        db_table = "core_idempotencyrecord"
        ordering = ["-created_at", "-id"]
        constraints = [
            # nulls_distinct=False makes a NULL clinic participate in uniqueness,
            # so a key reused without a clinic scope cannot create two records.
            models.UniqueConstraint(
                fields=["user", "clinic", "key"],
                name="core_idem_user_clinic_key_unique",
                nulls_distinct=False,
            ),
        ]
        indexes = [
            models.Index(fields=["expires_at"], name="core_idem_expires_idx"),
        ]
        verbose_name = "Idempotency record"
        verbose_name_plural = "Idempotency records"

    def __str__(self) -> str:
        return f"{self.method} {self.path} ({self.key})"


class AuditEvent(models.Model):
    """Append-only audit row, hash-chained per clinic.

    ``clinic`` is nullable because some events are platform-scoped (a login by
    an owner who belongs to several clinics, a password reset for a user with
    no clinic). Platform events share one chain keyed by ``platform``.

    ``before``/``after`` hold only redacted, non-PHI field snapshots (see
    ``core.redaction``). ``entity_id`` is a string so it can carry both UUID
    and integer primary keys.
    """

    id = models.BigAutoField(primary_key=True)
    clinic = models.ForeignKey(
        "clinics.Clinic",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    action = models.CharField(max_length=128)
    entity_type = models.CharField(max_length=128, blank=True, default="")
    entity_id = models.CharField(max_length=64, blank=True, default="")
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    request_id = models.CharField(max_length=128, blank=True, default="")
    ip_hash = models.CharField(max_length=64, blank=True, default="")
    prev_hash = models.CharField(max_length=64, blank=True, default="")
    hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "core_auditevent"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["clinic", "created_at"], name="audit_clinic_created_idx"),
            models.Index(
                fields=["clinic", "entity_type", "entity_id"],
                name="audit_clinic_entity_idx",
            ),
        ]
        verbose_name = "Audit event"
        verbose_name_plural = "Audit events"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Append-only: an instance loaded from the database already carries a
        # primary key and must never be written again.
        if self.pk is not None:
            raise AuditImmutableError("AuditEvent rows are append-only and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise AuditImmutableError("AuditEvent rows are append-only and cannot be deleted.")

    def __str__(self) -> str:
        return f"{self.action} #{self.pk}"


class JobRequest(models.Model):
    """Durable intent row behind every background task (T104, ADR-008).

    A service writes the row in the same transaction as its business change and
    dispatches ``core.jobs.run_job`` on commit. State lives here rather than in
    a Celery result backend, so a broker flush delays work instead of losing it.

    ``clinic`` is nullable: platform jobs (retention sweeps, model training)
    have no tenant. :class:`~apps.core.jobs.TenantJob`-style handlers must call
    ``with_clinic`` themselves; ``run_job`` does it automatically whenever
    ``clinic_id`` is set.

    ``payload`` and ``last_error`` carry ids, enum values and counters only.
    :func:`apps.core.jobs.enqueue` rejects secret and PII keys outright.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"
        DEAD = "dead", "Dead"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    clinic = models.ForeignKey(
        "clinics.Clinic",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="job_requests",
    )
    kind = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)
    idempotency_key = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    due_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.JSONField(null=True, blank=True)
    result = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "core_jobrequest"
        ordering = ["due_at", "created_at"]
        constraints = [
            # Partial unique: several NULL keys are fine (a job with no
            # idempotency key), but a concrete key is unique per clinic.
            models.UniqueConstraint(
                fields=["clinic", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="job_clinic_idem_unique",
                nulls_distinct=False,
            ),
        ]
        indexes = [
            models.Index(fields=["status", "due_at"], name="job_status_due_idx"),
            models.Index(fields=["clinic", "created_at"], name="job_clinic_created_idx"),
        ]
        verbose_name = "Job request"
        verbose_name_plural = "Job requests"

    def __str__(self) -> str:
        return f"{self.kind} [{self.status}] ({self.pk})"
