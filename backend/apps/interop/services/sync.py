"""Connector synchronisation: idempotent, resumable, fault-tolerant (T130).

The run pipeline is::

    resolve since -> SyncRun(running) -> pull patients (retry transient)
        -> upsert patients via ExternalIdMap -> pull appointments (retry)
        -> upsert appointments via ExternalIdMap -> finalise run

Guarantees
----------
* **Idempotent.** Every create is gated by an ``ExternalIdMap`` row keyed on
  ``(connector, entity_type, external_id)``; a second run creates nothing new.
* **Resumable.** The run stores a ``cursor`` (the newest processed record's
  timestamp). A partial/failed run is resumed from that cursor on the next call.
* **Accurate counts.** ``counts_json`` tallies created/skipped/conflicted rows
  and records every transient error that was retried.
* **Partial is partial.** A permanent failure in the appointment phase after the
  patient phase succeeded marks the run ``partial`` and records the created
  patient references; it never reports success it did not achieve.
* **Conflicts never overwrite.** An external id that resolves to a different
  internal row is queued in ``SyncConflict`` and skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.core.audit import record as record_audit
from apps.core.tenancy import with_clinic
from apps.patients.models import Patient
from apps.patients.services.numbering import allocate_patient_number

from ..connectors.base import (
    ConnectorError,
    ConnectorServerError,
    ExternalAppointment,
    ExternalPatient,
    PermanentConnectorError,
    TransientConnectorError,
)
from ..models import (
    ConnectorConfig,
    EntityType,
    ExternalIdMap,
    SyncRun,
    SyncStatus,
)
from . import ids as id_service
from . import reconcile as reconcile_service

MAX_ATTEMPTS = 3
DEFAULT_CURSOR = ""


@dataclass
class _Phase:
    records: list[Any] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    retries: int = 0
    fatal: ConnectorError | None = None

    @property
    def failed(self) -> bool:
        return self.fatal is not None


def _pull_with_retry(connector: Any, operation: str, since: datetime | None, attempts: int) -> _Phase:
    """Pull one entity kind, retrying only transient failures."""

    phase = _Phase()
    for attempt in range(1, max(1, attempts) + 1):
        try:
            phase.records = list(getattr(connector, operation)(since))
        except TransientConnectorError as exc:
            phase.errors.append(
                {"step": operation, "code": exc.code, "attempt": attempt, "message": exc.message}
            )
            phase.retries += 1
            continue
        except PermanentConnectorError as exc:
            phase.fatal = exc
            return phase
        except ConnectorError as exc:
            phase.fatal = exc
            return phase
        else:
            return phase

    phase.fatal = ConnectorServerError(f"{operation}: retries exhausted")
    phase.errors.append(
        {"step": operation, "code": phase.fatal.code, "attempt": attempts, "message": phase.fatal.message}
    )
    return phase


def _parse_dob(value: Any):
    if value is None:
        return None
    if hasattr(value, "year"):
        return value
    return parse_date(str(value))


def _create_patient(clinic: Any, record: ExternalPatient) -> Patient:
    with with_clinic(clinic):
        return Patient.all_objects.create(
            clinic_id=clinic.id,
            number=allocate_patient_number(clinic),
            first_name=(record.first_name or "")[:128],
            last_name=(record.last_name or "")[:128],
            date_of_birth=_parse_dob(record.date_of_birth),
            phone_e164=(record.phone or "")[:20],
            email=(record.email or "")[:254],
            source=Patient.Source.IMPORT,
        )


def _resolve_patient(connector: ConnectorConfig, record: ExternalPatient) -> tuple[Patient | None, str | None]:
    """Resolve or create the internal patient for ``record``.

    Returns ``(patient, conflict_reason)``. ``patient`` is None when the record
    could not be resolved (missing identity) or a conflict was queued.
    """

    clinic = connector.clinic
    if record.internal_id:
        declared = Patient.all_objects.filter(clinic=clinic, pk=record.internal_id).first()
        if declared is None:
            return None, reconcile_service.REASON_INTERNAL_MISMATCH
        return declared, None

    if record.number:
        existing = Patient.all_objects.filter(clinic=clinic, number=record.number).first()
        if existing is not None:
            other = ExternalIdMap.all_objects.filter(
                connector=connector,
                entity_type=EntityType.PATIENT,
                internal_id=existing.pk,
            ).exclude(external_id=record.external_id).first()
            if other is not None:
                return None, reconcile_service.REASON_EXTERNAL_REUSED
            return existing, None

    return _create_patient(clinic, record), None


def _upsert_patients(connector: ConnectorConfig, records: list[ExternalPatient]) -> dict[str, Any]:
    counts: dict[str, Any] = {
        "patients_created": 0,
        "patients_matched": 0,
        "patients_skipped": 0,
        "patients_conflicted": 0,
        "patients_invalid": 0,
    }
    references: list[str] = []

    for record in records:
        external_id = str(record.external_id or "").strip()
        if not external_id:
            counts["patients_invalid"] += 1
            continue

        mapping = id_service.get_mapping(connector, EntityType.PATIENT, external_id)
        if mapping is not None:
            if record.internal_id and str(record.internal_id) != str(mapping.internal_id):
                reconcile_service.queue_conflict(
                    connector,
                    entity_type=EntityType.PATIENT,
                    external_id=external_id,
                    internal_id=record.internal_id,
                )
                counts["patients_conflicted"] += 1
                continue
            counts["patients_skipped"] += 1
            continue

        patient, conflict_reason = _resolve_patient(connector, record)
        if conflict_reason is not None:
            reconcile_service.queue_conflict(
                connector,
                entity_type=EntityType.PATIENT,
                external_id=external_id,
                internal_id=record.internal_id,
                reason=conflict_reason,
            )
            counts["patients_conflicted"] += 1
            continue
        if patient is None:
            counts["patients_invalid"] += 1
            continue

        created_mapping, _created = id_service.create_mapping(
            connector, EntityType.PATIENT, external_id, patient.pk
        )
        if str(created_mapping.internal_id) != str(patient.pk):
            # Lost a race: another run mapped this external id to a different row.
            reconcile_service.queue_conflict(
                connector,
                entity_type=EntityType.PATIENT,
                external_id=external_id,
                internal_id=patient.pk,
            )
            counts["patients_conflicted"] += 1
            continue

        if record.internal_id or record.number:
            counts["patients_matched"] += 1
        else:
            counts["patients_created"] += 1
        references.append(str(patient.pk))

    counts["references"] = references
    return counts


def _resolve_appointment_deps(clinic: Any, record: ExternalAppointment) -> dict[str, Any] | None:
    from apps.clinics.models import Location, Provider
    from apps.scheduling.models import AppointmentType

    appointment_type = None
    if record.appointment_type_code:
        appointment_type = AppointmentType.all_objects.filter(
            clinic=clinic, code=record.appointment_type_code, is_active=True
        ).first()
    if appointment_type is None:
        appointment_type = (
            AppointmentType.all_objects.filter(clinic=clinic, is_active=True)
            .order_by("name", "id")
            .first()
        )

    provider = None
    if record.provider_name:
        provider = Provider.all_objects.filter(
            clinic=clinic, display_name=record.provider_name
        ).first()
    if provider is None:
        provider = (
            Provider.all_objects.filter(clinic=clinic, is_bookable=True)
            .order_by("display_name", "id")
            .first()
        )

    location = (
        Location.all_objects.filter(clinic=clinic, is_active=True).order_by("name", "id").first()
    )

    if appointment_type is None or provider is None or location is None:
        return None
    return {
        "appointment_type": appointment_type,
        "provider": provider,
        "location": location,
    }


def _upsert_appointments(
    connector: ConnectorConfig, records: list[ExternalAppointment]
) -> dict[str, Any]:
    from apps.scheduling.services.appointments import create_appointment

    counts: dict[str, Any] = {
        "appointments_created": 0,
        "appointments_skipped": 0,
        "appointments_conflicted": 0,
        "appointments_unresolved": 0,
        "appointments_invalid": 0,
    }
    references: list[str] = []
    clinic = connector.clinic

    for record in records:
        external_id = str(record.external_id or "").strip()
        if not external_id or record.start_at is None or record.end_at is None:
            counts["appointments_invalid"] += 1
            continue

        if id_service.get_mapping(connector, EntityType.APPOINTMENT, external_id) is not None:
            counts["appointments_skipped"] += 1
            continue

        patient_id = id_service.internal_id_for(
            connector, EntityType.PATIENT, record.patient_external_id
        )
        if patient_id is None:
            counts["appointments_unresolved"] += 1
            continue
        patient = Patient.all_objects.filter(clinic=clinic, pk=patient_id).first()
        if patient is None:
            counts["appointments_unresolved"] += 1
            continue

        deps = _resolve_appointment_deps(clinic, record)
        if deps is None:
            counts["appointments_unresolved"] += 1
            continue

        appointment = create_appointment(
            clinic,
            patient=patient,
            provider=deps["provider"],
            appointment_type=deps["appointment_type"],
            location=deps["location"],
            start_at=record.start_at,
            end_at=record.end_at,
            source="staff",
            reason=f"imported:{external_id}",
            idempotency_key=f"connector:{connector.id}:{external_id}",
        )
        created_mapping, _created = id_service.create_mapping(
            connector, EntityType.APPOINTMENT, external_id, appointment.pk
        )
        if str(created_mapping.internal_id) != str(appointment.pk):
            reconcile_service.queue_conflict(
                connector,
                entity_type=EntityType.APPOINTMENT,
                external_id=external_id,
                internal_id=appointment.pk,
            )
            counts["appointments_conflicted"] += 1
            continue
        counts["appointments_created"] += 1
        references.append(str(appointment.pk))

    counts["references"] = references
    return counts


def _resolve_since(connector: ConnectorConfig, since: datetime | None) -> datetime | None:
    if since is not None:
        return since
    last = (
        SyncRun.all_objects.filter(connector=connector)
        .order_by("-started_at", "-id")
        .first()
    )
    if last is not None and last.status in (SyncStatus.PARTIAL, SyncStatus.FAILED) and last.cursor:
        try:
            return datetime.fromisoformat(last.cursor)
        except ValueError:
            return None
    return None


def _max_cursor(records: list[Any]) -> str:
    stamps = [r.updated_at for r in records if getattr(r, "updated_at", None) is not None]
    if not stamps:
        return DEFAULT_CURSOR
    return max(stamps).isoformat()


def run_sync(
    connector: ConnectorConfig,
    *,
    since: datetime | None = None,
    request: Any = None,
    max_attempts: int = MAX_ATTEMPTS,
    actor: Any = None,
) -> SyncRun:
    """Run one pull synchronisation and return the finalised ``SyncRun``."""

    from ..connectors.base import get_connector

    started = timezone.now()
    # Resolve the resume cursor *before* creating the new run: the newest run
    # would otherwise be this still-running row.
    effective_since = _resolve_since(connector, since)
    run = SyncRun.all_objects.create(
        clinic_id=connector.clinic_id,
        connector=connector,
        direction="pull",
        status=SyncStatus.RUNNING,
        started_at=started,
    )

    connector_backend = get_connector(connector)

    errors: list[dict[str, Any]] = []
    retries = 0

    patients_phase = _pull_with_retry(
        connector_backend, "pull_patients", effective_since, max_attempts
    )
    errors.extend(patients_phase.errors)
    retries += patients_phase.retries

    patient_counts = {
        "patients_created": 0,
        "patients_matched": 0,
        "patients_skipped": 0,
        "patients_conflicted": 0,
        "patients_invalid": 0,
        "references": [],
    }
    if not patients_phase.failed:
        patient_counts = _upsert_patients(connector, patients_phase.records)

    appointment_counts = {
        "appointments_created": 0,
        "appointments_skipped": 0,
        "appointments_conflicted": 0,
        "appointments_unresolved": 0,
        "appointments_invalid": 0,
        "references": [],
    }
    appointments_phase = _Phase()
    if not patients_phase.failed:
        appointments_phase = _pull_with_retry(
            connector_backend, "pull_appointments", effective_since, max_attempts
        )
        errors.extend(appointments_phase.errors)
        retries += appointments_phase.retries
        if not appointments_phase.failed:
            appointment_counts = _upsert_appointments(connector, appointments_phase.records)

    # --- finalise ----------------------------------------------------------
    patients_created = patient_counts["patients_created"]
    fatal = patients_phase.fatal or appointments_phase.fatal

    if fatal is None:
        status = SyncStatus.SUCCEEDED
        error_code = ""
    else:
        error_code = fatal.code
        progressed = patients_created > 0 or patient_counts["patients_matched"] > 0
        if patients_phase.failed:
            status = SyncStatus.PARTIAL if progressed else SyncStatus.FAILED
        else:
            status = SyncStatus.PARTIAL if progressed else SyncStatus.FAILED

    counts = {
        **patient_counts,
        **appointment_counts,
        # Both phases carry a ``references`` key; merge rather than let the
        # appointment phase's empty list overwrite the patient references.
        "references": list(patient_counts.get("references", []))
        + list(appointment_counts.get("references", [])),
        "errors": errors,
        "retries": retries,
        "since": effective_since.isoformat() if effective_since is not None else None,
    }

    cursor = _max_cursor(list(patients_phase.records) + list(appointments_phase.records))

    run.status = status
    run.finished_at = timezone.now()
    run.counts_json = counts
    run.error_code = error_code
    run.cursor = cursor
    run.save(
        update_fields=["status", "finished_at", "counts_json", "error_code", "cursor", "updated_at"]
    )

    connector.last_sync_at = run.finished_at
    connector.save(update_fields=["last_sync_at", "updated_at"])

    action = {
        SyncStatus.SUCCEEDED: "connector.sync_succeeded",
        SyncStatus.PARTIAL: "connector.sync_partial",
        SyncStatus.FAILED: "connector.sync_failed",
    }.get(status, "connector.sync_failed")
    record_audit(
        action,
        entity=run,
        after={
            "connector_id": str(connector.id),
            "mode": connector.mode,
            "status": status,
            "error_code": error_code,
            "patients_created": patients_created,
            "appointments_created": appointment_counts["appointments_created"],
            "retries": retries,
        },
        actor=actor,
        request=request,
    )
    return run


def push_appointment(connector: ConnectorConfig, appointment: Any, *, request: Any = None) -> ExternalAppointment:
    """Push one appointment to the connector (protocol completeness)."""

    from ..connectors.base import get_connector

    backend = get_connector(connector)
    pushed = backend.push_appointment(appointment)
    record_audit(
        "connector.appointment_pushed",
        entity=connector,
        after={"connector_id": str(connector.id), "external_id": pushed.external_id},
        request=request,
    )
    return pushed


def push_status(connector: ConnectorConfig, appointment: Any, status: str, *, request: Any = None) -> None:
    """Push an appointment status change to the connector."""

    from ..connectors.base import get_connector

    backend = get_connector(connector)
    backend.push_status(appointment, status)
    record_audit(
        "connector.status_pushed",
        entity=connector,
        after={"connector_id": str(connector.id), "status": status},
        request=request,
    )


__all__ = [
    "MAX_ATTEMPTS",
    "push_appointment",
    "push_status",
    "run_sync",
]
