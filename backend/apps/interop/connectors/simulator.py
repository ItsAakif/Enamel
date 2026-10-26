"""Deterministic Open Dental simulator with fault injection (T130).

The simulator is the default connector (``mode=simulator``). Its seed data and
fault plan live in ``ConnectorConfig.settings_json``::

    {
      "patients":     [{"external_id": "E1", "first_name": "Ada", ...}],
      "appointments": [{"external_id": "A1", "patient_external_id": "E1",
                        "start_at": "...", "end_at": "...", "status": "scheduled"}],
      "faults": {
        "pull_patients":     {"timeout": 1, "error_500": 1},
        "pull_appointments": {"timeout": 0, "error_500": 0},
        "push_status":       {"error_500": 0},
        "duplicate_patients": false,
        "partial_write":      false
      }
    }

Fault semantics
---------------
* ``timeout`` / ``error_500`` are *transient* counters: each consumes one call,
  so a sync with retries recovers and records the error.
* ``partial_write`` is *permanent*: the appointment pull always fails after the
  patient phase succeeded, producing a ``partial`` run with patient references.
* ``duplicate_patients`` makes the patient pull emit every record twice, so the
  idempotency layer has something to deduplicate.

Nothing here ever opens a socket: the simulator is pure in-memory and therefore
safe under ``pytest-socket``.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from .base import (
    ConnectorPartialWrite,
    ConnectorServerError,
    ConnectorTimeout,
    ExternalAppointment,
    ExternalPatient,
)

DEFAULT_ERROR_MESSAGE = "Simulated Open Dental failure."


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class SimulatedOpenDental:
    """In-memory, deterministic Open Dental stand-in."""

    mode = "simulator"

    def __init__(
        self,
        *,
        patients: list[dict[str, Any]] | None = None,
        appointments: list[dict[str, Any]] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.settings = deepcopy(settings or {})
        self._patients = deepcopy(patients if patients is not None else self.settings.get("patients") or [])
        self._appointments = deepcopy(
            appointments if appointments is not None else self.settings.get("appointments") or []
        )
        self._faults = deepcopy(self.settings.get("faults") or {})
        self.calls: dict[str, int] = {}

    @classmethod
    def from_config(cls, config: Any) -> SimulatedOpenDental:
        settings_json = getattr(config, "settings_json", None) or {}
        return cls(settings=settings_json)

    # -- fault injection ----------------------------------------------------

    def _record_call(self, operation: str) -> None:
        self.calls[operation] = self.calls.get(operation, 0) + 1

    def _consume(self, operation: str, kind: str) -> None:
        plan = self._faults.get(operation)
        if not isinstance(plan, dict):
            return
        remaining = int(plan.get(kind, 0) or 0)
        if remaining <= 0:
            return
        plan[kind] = remaining - 1
        if kind == "timeout":
            raise ConnectorTimeout(f"{DEFAULT_ERROR_MESSAGE} (timeout)")
        if kind == "error_500":
            raise ConnectorServerError(f"{DEFAULT_ERROR_MESSAGE} (http 500)")

    def _partial_write(self) -> bool:
        return bool(self._faults.get("partial_write"))

    def _duplicate(self) -> bool:
        return bool(self._faults.get("duplicate_patients"))

    # -- protocol -----------------------------------------------------------

    def pull_patients(self, since: datetime | None = None) -> list[ExternalPatient]:
        self._record_call("pull_patients")
        self._consume("pull_patients", "timeout")
        self._consume("pull_patients", "error_500")

        records: list[ExternalPatient] = []
        for raw in self._patients:
            record = self._to_patient(raw)
            if since is not None and record.updated_at is not None and record.updated_at <= since:
                continue
            records.append(record)
            if self._duplicate():
                records.append(self._to_patient(raw))
        return records

    def pull_appointments(self, since: datetime | None = None) -> list[ExternalAppointment]:
        self._record_call("pull_appointments")
        self._consume("pull_appointments", "timeout")
        self._consume("pull_appointments", "error_500")
        if self._partial_write():
            raise ConnectorPartialWrite(
                "Simulated partial write: the appointment pull failed after patients."
            )

        records: list[ExternalAppointment] = []
        for raw in self._appointments:
            record = self._to_appointment(raw)
            if since is not None and record.updated_at is not None and record.updated_at <= since:
                continue
            records.append(record)
        return records

    def push_appointment(self, appointment: Any) -> ExternalAppointment:
        self._record_call("push_appointment")
        self._consume("push_appointment", "error_500")
        start_at = getattr(appointment, "start_at", None)
        end_at = getattr(appointment, "end_at", None)
        return ExternalAppointment(
            external_id=f"OD-APT-{getattr(appointment, 'pk', '')}",
            patient_external_id=f"OD-PAT-{getattr(appointment, 'patient_id', '')}",
            start_at=start_at,
            end_at=end_at,
            status=str(getattr(appointment, "status", "scheduled") or "scheduled"),
        )

    def push_status(self, appointment: Any, status: str) -> None:
        self._record_call("push_status")
        self._consume("push_status", "error_500")
        return None

    # -- raw -> typed -------------------------------------------------------

    @staticmethod
    def _to_patient(raw: dict[str, Any]) -> ExternalPatient:
        return ExternalPatient(
            external_id=str(raw.get("external_id", "")),
            first_name=str(raw.get("first_name", "") or ""),
            last_name=str(raw.get("last_name", "") or ""),
            date_of_birth=raw.get("date_of_birth"),
            phone=str(raw.get("phone", "") or ""),
            email=str(raw.get("email", "") or ""),
            number=raw.get("number"),
            internal_id=str(raw["internal_id"]) if raw.get("internal_id") else None,
            updated_at=_parse_dt(raw.get("updated_at")),
            raw=dict(raw),
        )

    @staticmethod
    def _to_appointment(raw: dict[str, Any]) -> ExternalAppointment:
        return ExternalAppointment(
            external_id=str(raw.get("external_id", "")),
            patient_external_id=str(raw.get("patient_external_id", "")),
            start_at=_parse_dt(raw.get("start_at")),
            end_at=_parse_dt(raw.get("end_at")),
            status=str(raw.get("status", "scheduled") or "scheduled"),
            provider_name=str(raw.get("provider_name", "") or ""),
            appointment_type_code=str(raw.get("appointment_type_code", "") or ""),
            updated_at=_parse_dt(raw.get("updated_at")),
            raw=dict(raw),
        )


__all__ = ["SimulatedOpenDental"]
