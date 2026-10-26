"""Dental recall due dates (T114).

A recall is due when the configured interval has elapsed since the patient's
last *completed* encounter/treatment. The baseline is the latest finalized
``patients.Encounter`` for the patient (any kind - exam, treatment or follow-up);
the due date is that instant plus the recall type's ``interval_months``.

``recall_due`` materialises a ``RecallStatus`` row per (patient with a completed
encounter, active recall type) so the due list is queryable and stable, and then
returns every row whose ``due_at`` has arrived. Scheduling a recall enqueues a
gated message through the dispatcher, so consent, suppression, quiet hours and
the frequency cap all still apply.
"""

from __future__ import annotations

import calendar
from datetime import datetime
from typing import Any

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.core.tenancy import with_clinic
from apps.patients.models import Encounter

from ..models import RecallStatus, RecallType
from . import dispatch

RECALL_PURPOSE = "recall"
DEFAULT_RECALL_TEMPLATE = "recall_reminder"


def add_months(moment: datetime, months: int) -> datetime:
    """Return ``moment`` advanced by ``months`` calendar months (clamped day)."""

    total = moment.month - 1 + int(months)
    year = moment.year + total // 12
    month = total % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def last_completed_at(patient: Any) -> datetime | None:
    """Latest finalized encounter instant for ``patient`` (or ``None``)."""

    return Encounter.all_objects.filter(
        clinic_id=patient.clinic_id,
        patient_id=patient.pk,
        status=Encounter.Status.FINALIZED,
    ).aggregate(last=Max("occurred_at"))["last"]


def due_at_for(last_completed: datetime | None, interval_months: int) -> datetime | None:
    """Due date = last completed + interval; ``None`` without a baseline."""

    if last_completed is None:
        return None
    return add_months(last_completed, interval_months)


def _entry(status: RecallStatus, recall_type: RecallType) -> dict[str, Any]:
    return {
        "id": str(status.id),
        "patient_id": str(status.patient_id),
        "recall_type_id": str(recall_type.id),
        "recall_type_name": recall_type.name,
        "channel": recall_type.channel,
        "template_key": recall_type.template_key,
        "interval_months": recall_type.interval_months,
        "due_at": status.due_at,
        "last_completed_at": status.last_completed_at,
        "status": status.status,
    }


def _sync_status(
    *,
    clinic: Any,
    patient_id: Any,
    recall_type: RecallType,
    last_completed: datetime,
    create_missing: bool,
) -> RecallStatus | None:
    due_at = due_at_for(last_completed, recall_type.interval_months)
    if due_at is None:
        return None
    status = RecallStatus.all_objects.filter(
        clinic=clinic, patient_id=patient_id, recall_type=recall_type
    ).first()
    if status is None:
        if not create_missing:
            return None
        return RecallStatus.all_objects.create(
            clinic=clinic,
            patient_id=patient_id,
            recall_type=recall_type,
            due_at=due_at,
            last_completed_at=last_completed,
            status=RecallStatus.Status.DUE,
        )
    if status.last_completed_at != last_completed and status.status in (
        RecallStatus.Status.DUE,
        RecallStatus.Status.SCHEDULED,
    ):
        status.last_completed_at = last_completed
        status.due_at = due_at
        status.save(update_fields=["last_completed_at", "due_at", "updated_at"])
    return status


def recall_due(
    clinic: Any,
    as_of: datetime | None = None,
    *,
    create_missing: bool = True,
) -> list[dict[str, Any]]:
    """Return every recall that is due at ``as_of`` (clinic-local window)."""

    moment = as_of or timezone.now()
    due: list[dict[str, Any]] = []

    with transaction.atomic():
        with with_clinic(clinic):
            recall_types = list(
                RecallType.all_objects.filter(clinic=clinic, is_active=True).order_by(
                    "name", "id"
                )
            )
            if not recall_types:
                return []

            completed = (
                Encounter.all_objects.filter(
                    clinic_id=getattr(clinic, "id", clinic),
                    status=Encounter.Status.FINALIZED,
                )
                .values("patient_id")
                .annotate(last=Max("occurred_at"))
            )
            for row in completed:
                last_completed = row["last"]
                if last_completed is None:
                    continue
                for recall_type in recall_types:
                    _sync_status(
                        clinic=clinic,
                        patient_id=row["patient_id"],
                        recall_type=recall_type,
                        last_completed=last_completed,
                        create_missing=create_missing,
                    )

            rows = (
                RecallStatus.all_objects.filter(
                    clinic=clinic,
                    status__in=[RecallStatus.Status.DUE, RecallStatus.Status.SCHEDULED],
                    due_at__lte=moment,
                )
                .select_related("recall_type")
                .order_by("due_at", "id")
            )
            for status in rows:
                due.append(_entry(status, status.recall_type))
    return due


def schedule(
    patient: Any,
    recall_type: RecallType,
    *,
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mark a recall scheduled and enqueue one gated message."""

    clinic = patient.clinic
    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(clinic):
            last_completed = last_completed_at(patient)
            due_at = due_at_for(last_completed, recall_type.interval_months) or moment
            status = RecallStatus.all_objects.filter(
                clinic=clinic, patient_id=patient.pk, recall_type=recall_type
            ).first()
            if status is None:
                status = RecallStatus.all_objects.create(
                    clinic=clinic,
                    patient_id=patient.pk,
                    recall_type=recall_type,
                    due_at=due_at,
                    last_completed_at=last_completed,
                    status=RecallStatus.Status.SCHEDULED,
                )
            else:
                status.due_at = due_at
                status.last_completed_at = last_completed
                status.status = RecallStatus.Status.SCHEDULED
                status.save(
                    update_fields=["due_at", "last_completed_at", "status", "updated_at"]
                )

            context = {
                "first_name": patient.first_name,
                "recall_name": recall_type.name,
                "clinic": clinic.name,
            }
            message = dispatch.send(
                patient,
                recall_type.channel,
                RECALL_PURPOSE,
                recall_type.template_key or DEFAULT_RECALL_TEMPLATE,
                context,
                idempotency_key=(
                    f"recall:{patient.pk}:{recall_type.pk}:{due_at.date().isoformat()}"
                ),
                now=moment,
                actor=actor,
                request=request,
            )
    return {"recall_status_id": str(status.id), "message_id": str(message.id), "status": message.status}


def complete(
    patient: Any,
    recall_type: RecallType,
    *,
    completed_at: datetime | None = None,
) -> RecallStatus:
    """Record a completed recall and reset the due date from ``completed_at``."""

    clinic = patient.clinic
    moment = completed_at or timezone.now()
    with transaction.atomic():
        with with_clinic(clinic):
            status = RecallStatus.all_objects.filter(
                clinic=clinic, patient_id=patient.pk, recall_type=recall_type
            ).first()
            due_at = due_at_for(moment, recall_type.interval_months) or moment
            if status is None:
                status = RecallStatus.all_objects.create(
                    clinic=clinic,
                    patient_id=patient.pk,
                    recall_type=recall_type,
                    due_at=due_at,
                    last_completed_at=moment,
                    status=RecallStatus.Status.COMPLETED,
                )
            else:
                status.last_completed_at = moment
                status.due_at = due_at
                status.status = RecallStatus.Status.COMPLETED
                status.save(
                    update_fields=["last_completed_at", "due_at", "status", "updated_at"]
                )
    return status


__all__ = [
    "DEFAULT_RECALL_TEMPLATE",
    "RECALL_PURPOSE",
    "add_months",
    "complete",
    "due_at_for",
    "last_completed_at",
    "recall_due",
    "schedule",
]
