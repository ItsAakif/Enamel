"""Chart reads and status transitions (T108).

``build_chart`` is the only read path for the odontogram: it returns the
patient-level dentition (derived from age, or an explicit override), one entry
per tooth in that dentition, the active condition layers on each tooth, counts
and the condition legend. Only non-archived states are ever returned.

``transition_state`` moves a layer through the state machine
(``existing``/``planned``/``referred``/``completed`` plus ``archived``) and
writes the history event and audit row in the same transaction.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.tenancy import with_clinic

from .. import fdi
from ..conditions import legend
from ..models import ConditionCode, ToothState
from .history import record_event, state_snapshot

# The status machine. ``archived`` is a pseudo-target that stamps
# ``archived_at`` rather than a stored status value.
TRANSITIONS: dict[str, frozenset[str]] = {
    "existing": frozenset({"planned", "referred", "archived"}),
    "planned": frozenset({"completed", "referred", "existing", "archived"}),
    "referred": frozenset({"planned", "existing", "archived"}),
    "completed": frozenset({"archived"}),
}


def resolve_dentition(
    patient: Any,
    override: str | None = None,
    as_of: Any = None,
) -> tuple[str, str]:
    """Return ``(dentition, source)`` where source is ``derived`` or ``override``."""

    if override:
        if override not in fdi.CHART_DENTITIONS:
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=400,
                field_errors={
                    "dentition": [f"select one of: {', '.join(fdi.CHART_DENTITIONS)}"]
                },
            )
        return override, "override"
    reference = as_of if as_of is not None else timezone.localdate()
    return fdi.derived_dentition(patient.date_of_birth, reference), "derived"


def state_payload(state: ToothState) -> dict[str, Any]:
    """Wire shape of one active condition layer."""

    return {
        "id": str(state.id),
        "tooth": state.tooth,
        "condition_code": state.condition_id,
        "status": state.status,
        "surfaces": list(state.surfaces or []),
        "severity": state.severity,
        "material": state.material,
        "note": state.note,
        "version": state.version,
        "plan_item_id": str(state.plan_item_id) if state.plan_item_id else None,
    }


# Fields that may appear in an audit payload. ``note`` is absent on purpose:
# ``core.redaction`` masks keys named ``notes`` and ``*_notes``, but the model
# field is the singular ``note``, so the value is simply never copied instead of
# relying on a key-name heuristic.
_AUDIT_FIELDS: tuple[str, ...] = (
    "tooth",
    "condition_code",
    "status",
    "surfaces",
    "severity",
    "material",
    "version",
    "plan_item_id",
)


def audit_from_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Extract the redaction-safe audit fields from a state snapshot."""

    snapshot = snapshot or {}
    return {name: snapshot.get(name) for name in _AUDIT_FIELDS if name in snapshot}


def audit_state(state: ToothState) -> dict[str, Any]:
    """Redaction-safe audit snapshot of a live state (note excluded)."""

    return audit_from_snapshot(state_snapshot(state))


def active_states(patient: Any, *, lock: bool = False) -> list[ToothState]:
    """Active (non-archived) layers for a patient, scoped to its clinic."""

    with with_clinic(patient.clinic):
        queryset = ToothState.objects.filter(patient=patient, archived_at__isnull=True)
        if lock:
            queryset = queryset.select_for_update()
        rows = list(
            queryset.select_related("condition").order_by(
                "tooth", "condition__sort_order", "id"
            )
        )
    return rows


def build_chart(
    patient: Any,
    *,
    dentition: str | None = None,
    as_of: Any = None,
) -> dict[str, Any]:
    """Assemble the full odontogram payload for ``patient``."""

    resolved, source = resolve_dentition(patient, dentition, as_of)
    states = active_states(patient)

    by_tooth: dict[int, list[ToothState]] = {}
    for state in states:
        by_tooth.setdefault(state.tooth, []).append(state)

    teeth: list[dict[str, Any]] = []
    seen_codes: set[int] = set()
    for code in fdi.codes_for_dentition(resolved):
        seen_codes.add(code)
        teeth.append(
            {
                "tooth": code,
                "info": fdi.tooth_info(code),
                "states": [state_payload(state) for state in by_tooth.get(code, [])],
            }
        )
    # Never silently drop a persisted layer just because it falls outside the
    # resolved dentition (an adult with an override of "primary", say).
    for code in sorted(set(by_tooth) - seen_codes):
        teeth.append(
            {
                "tooth": code,
                "info": fdi.tooth_info(code),
                "states": [state_payload(state) for state in by_tooth[code]],
            }
        )
    teeth.sort(key=lambda entry: entry["tooth"])

    counts = counts_for_states(states)
    return {
        "dentition": resolved,
        "dentition_source": source,
        "teeth": teeth,
        "counts": counts,
        "legend": legend(),
    }


def counts_for_states(states: list[ToothState]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_condition: dict[str, int] = {}
    for state in states:
        by_status[state.status] = by_status.get(state.status, 0) + 1
        by_condition[state.condition_id] = by_condition.get(state.condition_id, 0) + 1
    return {
        "total": len(states),
        "by_status": by_status,
        "by_condition": by_condition,
        "teeth_with_states": len({state.tooth for state in states}),
    }


def transition_state(
    state: ToothState,
    *,
    to_status: str,
    plan_item_id: Any = None,
    actor: Any = None,
    request: Any = None,
) -> ToothState:
    """Move a layer to ``to_status`` and record the event + audit row."""

    if to_status not in (*TRANSITIONS, "archived"):
        raise ApiError(
            "invalid_transition",
            "That status transition is not allowed.",
            status_code=409,
            details={"to": to_status},
        )

    with transaction.atomic():
        with with_clinic(state.clinic):
            locked = (
                ToothState.objects.select_for_update()
                .select_related("condition")
                .get(pk=state.pk)
            )
            if locked.archived_at is not None:
                raise ApiError(
                    "invalid_transition",
                    "That status transition is not allowed.",
                    status_code=409,
                    details={"reason": "archived", "to": to_status},
                )

            before = state_snapshot(locked)
            now = timezone.now()

            if to_status == "archived":
                locked.archived_at = now
                locked.version += 1
                locked.updated_by = actor
                locked.save(update_fields=["archived_at", "version", "updated_by", "updated_at"])
                after = state_snapshot(locked)
                record_event(
                    state=locked,
                    patient=locked.patient,
                    event_type="archived",
                    before=before,
                    after=after,
                    version=locked.version,
                    actor=actor,
                )
                record_audit(
                    "chart.archived",
                    entity=locked,
                    before=audit_from_snapshot(before),
                    after={**audit_from_snapshot(after), "changed_fields": ["archived_at"]},
                    actor=actor,
                    request=request,
                )
                return locked

            allowed = TRANSITIONS.get(locked.status, frozenset())
            if to_status not in allowed:
                raise ApiError(
                    "invalid_transition",
                    "That status transition is not allowed.",
                    status_code=409,
                    field_errors={"to_status": [f"{locked.status} -> {to_status} is not allowed"]},
                    details={"from": locked.status, "to": to_status},
                )

            if locked.status == "planned" and to_status == "completed" and not plan_item_id:
                raise ApiError(
                    "plan_item_missing",
                    "Completing a planned treatment requires a plan item.",
                    status_code=409,
                    field_errors={"plan_item_id": ["required to complete a planned state"]},
                )

            changed = {"status": to_status}
            locked.status = to_status
            if plan_item_id is not None:
                locked.plan_item_id = plan_item_id
                changed["plan_item_id"] = str(plan_item_id)
            locked.version += 1
            locked.updated_by = actor
            locked.save(
                update_fields=["status", "plan_item_id", "version", "updated_by", "updated_at"]
            )
            after = state_snapshot(locked)
            record_event(
                state=locked,
                patient=locked.patient,
                event_type="transitioned",
                before=before,
                after=after,
                version=locked.version,
                actor=actor,
            )
            record_audit(
                "chart.updated",
                entity=locked,
                before=audit_from_snapshot(before),
                after={**audit_from_snapshot(after), "changed_fields": sorted(changed)},
                actor=actor,
                request=request,
            )
            return locked


def condition_catalogue() -> dict[str, ConditionCode]:
    return {row.code: row for row in ConditionCode.objects.all()}


__all__ = [
    "TRANSITIONS",
    "active_states",
    "audit_from_snapshot",
    "audit_state",
    "build_chart",
    "condition_catalogue",
    "counts_for_states",
    "resolve_dentition",
    "state_payload",
    "transition_state",
]
