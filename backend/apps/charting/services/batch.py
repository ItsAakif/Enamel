"""Atomic batch save for the odontogram (T108).

A single transaction does all of this, in this order:

1. Refuse an archived/deleted patient (403 ``patient_archived``).
2. ``SELECT ... FOR UPDATE`` the patient row. Every writer for a patient takes
   this lock first, which serialises concurrent batches and is what makes the
   optimistic ``version`` check race-free: the loser re-reads the row after the
   winner commits and sees the new version.
3. Replay a stored response for the same ``(clinic, idempotency_key)``.
4. Lock the patient's existing layers and index them by ``(tooth, condition)``.
5. Validate *every* change (dentition, condition code, surfaces, severity,
   status, version) and collect field-addressed errors. If anything is wrong,
   raise before a single row is written.
6. Apply the changes, writing one history event and one audit row each.
7. Store the response under the idempotency key.

Because 5 precedes 6 and the whole thing is one transaction, "any invalid item
writes zero rows" holds by construction as well as by rollback.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.tenancy import with_clinic
from apps.patients.models import Patient

from .. import fdi, validators
from ..models import ChartBatchLog, ConditionCode, ToothState
from .chart import (
    active_states,
    audit_from_snapshot,
    counts_for_states,
    resolve_dentition,
    state_payload,
)
from .history import record_event, state_snapshot

_CONFLICT_MESSAGE = "The state changed since you loaded it."


def _applied(
    tooth: int,
    condition_code: str,
    action: str,
    state: ToothState | None,
) -> dict[str, Any]:
    return {
        "tooth": tooth,
        "condition_code": condition_code,
        "action": action,
        "id": str(state.id) if state is not None else None,
        "version": state.version if state is not None else None,
        "status": state.status if state is not None else None,
    }


def _condition_catalogue() -> dict[str, ConditionCode]:
    return {row.code: row for row in ConditionCode.objects.all()}


def _validate(
    patient: Patient,
    changes: list[dict[str, Any]],
    *,
    dentition: str,
    index: dict[tuple[int, str], ToothState],
    catalogue: dict[str, ConditionCode],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Validate every change; returns ``(normalized, field_errors)``."""

    normalized: list[dict[str, Any]] = []
    field_errors: dict[str, list[str]] = {}

    for position, change in enumerate(changes):
        prefix = f"changes.{position}"
        errors: dict[str, list[str]] = {}

        info = None
        try:
            info = fdi.validate_tooth(change.get("tooth"), dentition)
        except ValueError as exc:
            errors[f"{prefix}.tooth"] = [str(exc)]

        code = str(change.get("condition_code") or "")
        condition = catalogue.get(code)
        if condition is None:
            errors[f"{prefix}.condition_code"] = ["unknown condition code"]
        elif not condition.is_active:
            errors[f"{prefix}.condition_code"] = ["condition code is not active"]

        if info is not None and not change.get("archive"):
            surfaces = change.get("surfaces", [])
            try:
                validators.validate_surfaces(surfaces, info, field=f"{prefix}.surfaces")
            except validators.ChartValidationError as exc:
                errors.update(exc.field_errors)

            # A missing / extraction-planned tooth may not take surface edits
            # unless the change *is* that condition.
            if surfaces and info is not None:
                blocked = [
                    state
                    for (tooth, cond), state in index.items()
                    if tooth == info["code"] and cond in validators.MISSING_CONDITIONS
                ]
                if blocked and code not in validators.MISSING_CONDITIONS:
                    errors.setdefault(f"{prefix}.surfaces", []).append(
                        "no surface edits on a missing or extraction-planned tooth"
                    )

        if "severity" in change:
            try:
                validators.validate_severity(change["severity"], field=f"{prefix}.severity")
            except validators.ChartValidationError as exc:
                errors.update(exc.field_errors)

        if change.get("status") is not None:
            try:
                validators.validate_status(change["status"], field=f"{prefix}.status")
            except validators.ChartValidationError as exc:
                errors.update(exc.field_errors)

        version = change.get("version")
        if version is not None and (isinstance(version, bool) or int(version) < 1):
            errors[f"{prefix}.version"] = ["enter a positive version"]

        field_errors.update(errors)
        normalized.append({"change": change, "info": info, "condition": condition})

    return normalized, field_errors


def apply_batch(
    patient: Patient,
    *,
    changes: list[dict[str, Any]],
    idempotency_key: str,
    dentition: str | None = None,
    force: bool = False,
    actor: Any = None,
    request: Any = None,
    as_of: Any = None,
) -> dict[str, Any]:
    """Apply ``changes`` atomically; returns the response payload."""

    if patient.is_archived:
        raise ApiError(
            "patient_archived",
            "This patient record is archived and cannot be modified.",
            status_code=403,
        )

    with transaction.atomic():
        # Serialise every writer for this patient. ``all_objects`` because the
        # lock is a row operation, not a tenant read.
        Patient.all_objects.select_for_update().filter(pk=patient.pk).first()

        with with_clinic(patient.clinic):
            stored = ChartBatchLog.objects.filter(key=idempotency_key).first()
            if stored is not None:
                replayed = dict(stored.result or {})
                replayed["replayed"] = True
                return replayed

            resolved, _source = resolve_dentition(patient, dentition, as_of)

            locked = list(
                ToothState.objects.select_for_update()
                .select_related("condition")
                .filter(patient=patient, archived_at__isnull=True)
            )
            index: dict[tuple[int, str], ToothState] = {
                (state.tooth, state.condition_id): state for state in locked
            }

            catalogue = _condition_catalogue()
            normalized, field_errors = _validate(
                patient,
                changes,
                dentition=resolved,
                index=index,
                catalogue=catalogue,
            )
            if field_errors:
                raise ApiError(
                    "validation_error",
                    "The request could not be processed.",
                    status_code=400,
                    field_errors=field_errors,
                )

            batch_id = uuid.uuid7()
            applied = _apply(
                patient,
                normalized,
                index=index,
                force=force,
                actor=actor,
                request=request,
                idempotency_key=idempotency_key,
                batch_id=batch_id,
            )

            final_states = active_states(patient)
            result: dict[str, Any] = {
                "applied": applied,
                "states": [state_payload(state) for state in final_states],
                "dentition": resolved,
                "counts": counts_for_states(final_states),
                "replayed": False,
            }
            ChartBatchLog.objects.create(
                clinic=patient.clinic,
                patient=patient,
                key=idempotency_key,
                result=result,
            )
            return result


def _apply(
    patient: Patient,
    normalized: list[dict[str, Any]],
    *,
    index: dict[tuple[int, str], ToothState],
    force: bool,
    actor: Any,
    request: Any,
    idempotency_key: str,
    batch_id: Any,
) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    now = timezone.now()

    for position, item in enumerate(normalized):
        change = item["change"]
        info = item["info"]
        condition = item["condition"]
        tooth = info["code"]
        code = condition.code
        key = (tooth, code)
        state = index.get(key)

        if change.get("archive"):
            if state is None:
                applied.append(_applied(tooth, code, "unchanged", None))
                continue
            before = state_snapshot(state)
            state.archived_at = now
            state.version += 1
            state.updated_by = actor
            state.save(update_fields=["archived_at", "version", "updated_by", "updated_at"])
            after = state_snapshot(state)
            record_event(
                state=state,
                patient=patient,
                event_type="archived",
                before=before,
                after=after,
                version=state.version,
                actor=actor,
                idempotency_key=idempotency_key,
                batch_id=batch_id,
                occurred_at=now,
            )
            record_audit(
                "chart.archived",
                entity=state,
                before=audit_from_snapshot(before),
                after={**audit_from_snapshot(after), "changed_fields": ["archived_at"]},
                actor=actor,
                request=request,
            )
            index.pop(key, None)
            applied.append(_applied(tooth, code, "archived", state))
            continue

        if state is None:
            state = ToothState(
                clinic=patient.clinic,
                patient=patient,
                tooth=tooth,
                dentition=info["dentition"],
                condition_id=code,
                status=change.get("status") or condition.default_status,
                surfaces=list(change.get("surfaces", [])),
                severity=change.get("severity"),
                material=change.get("material", ""),
                note=change.get("note", ""),
                version=1,
                updated_by=actor,
            )
            state.save()
            index[key] = state
            after = state_snapshot(state)
            record_event(
                state=state,
                patient=patient,
                event_type="created",
                before=None,
                after=after,
                version=1,
                actor=actor,
                idempotency_key=idempotency_key,
                batch_id=batch_id,
                occurred_at=now,
            )
            record_audit(
                "chart.created",
                entity=state,
                after={
                    **audit_from_snapshot(after),
                    "changed_fields": sorted(
                        name
                        for name in ("status", "surfaces", "severity", "material", "note")
                        if name in change
                    ),
                },
                actor=actor,
                request=request,
            )
            applied.append(_applied(tooth, code, "created", state))
            continue

        version = change.get("version")
        if version is not None and int(version) != state.version:
            raise ApiError(
                "version_conflict",
                _CONFLICT_MESSAGE,
                status_code=409,
                field_errors={
                    f"changes.{position}.version": [
                        f"expected version {state.version}, got {int(version)}"
                    ]
                },
                details={
                    "current": state_payload(state),
                    "tooth": tooth,
                    "condition_code": code,
                },
            )

        if version is None and not force:
            # Re-adding a condition that is already active is idempotent: return
            # the existing layer untouched. ``force=true`` opts into an update.
            applied.append(_applied(tooth, code, "unchanged", state))
            continue

        before = state_snapshot(state)
        changed: list[str] = []
        for name in ("status", "surfaces", "severity", "material", "note"):
            if name in change:
                value = change[name]
                if name == "surfaces":
                    value = list(value or [])
                setattr(state, name, value)
                changed.append(name)
        state.version += 1
        state.updated_by = actor
        state.save(
            update_fields=[*changed, "version", "updated_by", "updated_at"]
        )
        after = state_snapshot(state)
        record_event(
            state=state,
            patient=patient,
            event_type="updated",
            before=before,
            after=after,
            version=state.version,
            actor=actor,
            idempotency_key=idempotency_key,
            batch_id=batch_id,
            occurred_at=now,
        )
        record_audit(
            "chart.updated",
            entity=state,
            before=audit_from_snapshot(before),
            after={**audit_from_snapshot(after), "changed_fields": sorted(changed)},
            actor=actor,
            request=request,
        )
        applied.append(_applied(tooth, code, "updated", state))

    return applied


__all__ = ["apply_batch"]
