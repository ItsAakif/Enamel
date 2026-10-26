"""Treatment plan pricing and lifecycle (T117).

Two invariants drive this module:

* **The server owns the money.** Every ``PlanItem.line_total_minor`` and every
  ``TreatmentPlan`` total is recomputed here from the item inputs with integer
  minor units and half-up rounding; a client-supplied total is discarded.
* **The lifecycle is a validated state machine.** Only the transitions in
  :data:`ALLOWED_TRANSITIONS` are legal; anything else raises a 409
  ``invalid_transition``. Completing an item can also flip the linked
  odontogram ``ToothState`` to ``completed`` inside the same transaction, so a
  reader never sees a completed procedure whose tooth layer is still ``planned``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.charting.models import ToothState
from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.money import line_subtotal_minor, line_tax_minor, line_total_minor
from apps.core.tenancy import with_clinic

from ..models import PlanItem, TreatmentPlan

SNAPSHOT_FIELDS = (
    "status",
    "currency",
    "subtotal_minor",
    "discount_minor",
    "tax_minor",
    "total_minor",
    "version",
)

#: Legal plan status transitions (current -> allowed targets).
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    TreatmentPlan.Status.DRAFT: {
        TreatmentPlan.Status.PRESENTED,
        TreatmentPlan.Status.CANCELLED,
    },
    TreatmentPlan.Status.PRESENTED: {
        TreatmentPlan.Status.ACCEPTED,
        TreatmentPlan.Status.DECLINED,
        TreatmentPlan.Status.CANCELLED,
    },
    TreatmentPlan.Status.ACCEPTED: {
        TreatmentPlan.Status.COMPLETED,
        TreatmentPlan.Status.CANCELLED,
    },
    TreatmentPlan.Status.DECLINED: set(),
    TreatmentPlan.Status.COMPLETED: set(),
    TreatmentPlan.Status.CANCELLED: set(),
}

#: ``POST /treatment-plans/{id}/{action}/`` -> target status.
ACTION_TARGETS: dict[str, str] = {
    "present": TreatmentPlan.Status.PRESENTED,
    "accept": TreatmentPlan.Status.ACCEPTED,
    "decline": TreatmentPlan.Status.DECLINED,
}

#: Fields an edit through the API may change on the plan itself.
PLAN_EDITABLE_FIELDS = ("title", "notes", "provider", "expires_at")

#: Fields accepted for one item on create/replace.
ITEM_FIELDS = (
    "procedure_code",
    "description",
    "tooth",
    "surfaces",
    "quantity",
    "unit_price_minor",
    "discount_minor",
    "tax_rate_bp",
    "status",
    "tooth_state_id",
    "notes",
)


def snapshot(plan: TreatmentPlan) -> dict[str, Any]:
    """Audit projection for a plan (enums and money only, no free text)."""

    return {name: getattr(plan, name) for name in SNAPSHOT_FIELDS}


def item_snapshot(item: PlanItem) -> dict[str, Any]:
    return {
        "procedure_code": item.procedure_code,
        "tooth": item.tooth,
        "quantity": item.quantity,
        "unit_price_minor": item.unit_price_minor,
        "discount_minor": item.discount_minor,
        "tax_rate_bp": item.tax_rate_bp,
        "line_total_minor": item.line_total_minor,
        "status": item.status,
        "sequence": item.sequence,
    }


def items_for(plan: TreatmentPlan) -> list[PlanItem]:
    return list(
        PlanItem.all_objects.filter(plan_id=plan.pk).order_by("sequence", "id")
    )


def compute_totals(items: list[PlanItem]) -> dict[str, int]:
    """Return the plan totals derived from already-stored item inputs."""

    subtotal = sum(line_subtotal_minor(i.quantity, i.unit_price_minor) for i in items)
    discount = sum(i.discount_minor for i in items)
    tax = sum(
        line_tax_minor(i.quantity, i.unit_price_minor, i.discount_minor, i.tax_rate_bp)
        for i in items
    )
    total = sum(
        line_total_minor(i.quantity, i.unit_price_minor, i.discount_minor, i.tax_rate_bp)
        for i in items
    )
    return {
        "subtotal_minor": subtotal,
        "discount_minor": discount,
        "tax_minor": tax,
        "total_minor": total,
    }


def recompute(plan: TreatmentPlan) -> TreatmentPlan:
    """Recompute every item line total and the plan totals in place."""

    items = items_for(plan)
    for item in items:
        total = line_total_minor(
            item.quantity, item.unit_price_minor, item.discount_minor, item.tax_rate_bp
        )
        if item.line_total_minor != total:
            item.line_total_minor = total
            item.save(update_fields=["line_total_minor", "updated_at"])

    totals = compute_totals(items)
    changed = False
    for field, value in totals.items():
        if getattr(plan, field) != value:
            setattr(plan, field, value)
            changed = True
    if changed:
        plan.save(update_fields=[*totals.keys(), "updated_at"])
    return plan


def _create_item(plan: TreatmentPlan, spec: dict[str, Any], sequence: int) -> PlanItem:
    data = {name: spec.get(name) for name in ITEM_FIELDS if name in spec}
    return PlanItem.all_objects.create(
        clinic_id=plan.clinic_id,
        plan=plan,
        sequence=sequence,
        procedure_code=data.get("procedure_code", ""),
        description=data.get("description", "") or "",
        tooth=data.get("tooth"),
        surfaces=data.get("surfaces") or [],
        quantity=data.get("quantity", 1),
        unit_price_minor=data.get("unit_price_minor", 0),
        discount_minor=data.get("discount_minor", 0),
        tax_rate_bp=data.get("tax_rate_bp", 0),
        status=data.get("status", PlanItem.Status.PROPOSED),
        tooth_state_id=data.get("tooth_state_id"),
        notes=data.get("notes", "") or "",
        # ``line_total_minor`` is deliberately not taken from the client.
        line_total_minor=0,
    )


def replace_items(plan: TreatmentPlan, items: list[dict[str, Any]]) -> None:
    """Replace every item on ``plan`` (draft only) and renumber sequences."""

    PlanItem.all_objects.filter(plan_id=plan.pk).delete()
    for index, spec in enumerate(items):
        _create_item(plan, spec, index)


def create_plan(
    clinic: Any,
    *,
    patient: Any,
    title: str,
    items: list[dict[str, Any]],
    currency: str | None = None,
    notes: str = "",
    provider: Any = None,
    expires_at: datetime | None = None,
    actor: Any = None,
    request: Any = None,
) -> TreatmentPlan:
    """Create a plan, its items and the recomputed totals in one transaction."""

    with transaction.atomic():
        with with_clinic(clinic):
            plan = TreatmentPlan.all_objects.create(
                clinic_id=clinic.id,
                patient=patient,
                title=title,
                currency=currency or clinic.currency,
                notes=notes,
                provider=provider,
                expires_at=expires_at,
                status=TreatmentPlan.Status.DRAFT,
            )
            replace_items(plan, items)
            recompute(plan)
            record_audit(
                "plan.created",
                entity=plan,
                after=snapshot(plan),
                actor=actor,
                request=request,
            )
    return plan


def update_plan(
    plan: TreatmentPlan,
    *,
    fields: dict[str, Any],
    items: list[dict[str, Any]] | None = None,
    actor: Any = None,
    request: Any = None,
) -> TreatmentPlan:
    """Apply edits to a plan; item replacement is allowed while ``draft``."""

    with transaction.atomic():
        with with_clinic(plan.clinic):
            before = snapshot(plan)
            for name in PLAN_EDITABLE_FIELDS:
                if name in fields:
                    setattr(plan, name, fields[name])
            plan.version = plan.version + 1
            plan.save(update_fields=[*PLAN_EDITABLE_FIELDS, "version", "updated_at"])

            if items is not None:
                if plan.status != TreatmentPlan.Status.DRAFT:
                    raise ApiError(
                        "invalid_transition",
                        "Items can only be edited while the plan is a draft.",
                        status_code=409,
                        details={"status": plan.status},
                    )
                replace_items(plan, items)
            recompute(plan)
            record_audit(
                "plan.updated",
                entity=plan,
                before=before,
                after=snapshot(plan),
                actor=actor,
                request=request,
            )
    return plan


def transition(
    plan: TreatmentPlan,
    action: str,
    *,
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> TreatmentPlan:
    """Apply ``present``/``accept``/``decline`` with validation."""

    target = ACTION_TARGETS.get(action)
    if target is None:
        raise ApiError("not_found", "Not found.", status_code=404)

    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(plan.clinic):
            locked = TreatmentPlan.all_objects.select_for_update().get(pk=plan.pk)
            if target not in ALLOWED_TRANSITIONS.get(locked.status, set()):
                raise ApiError(
                    "invalid_transition",
                    "This plan cannot make that transition.",
                    status_code=409,
                    details={"from": locked.status, "to": target, "action": action},
                )
            before = snapshot(locked)
            update_fields = ["status", "version", "updated_at"]
            locked.status = target
            locked.version = locked.version + 1
            if target == TreatmentPlan.Status.PRESENTED:
                locked.presented_at = moment
                update_fields.append("presented_at")
            elif target == TreatmentPlan.Status.ACCEPTED:
                locked.accepted_at = moment
                locked.accepted_by = actor
                update_fields.extend(["accepted_at", "accepted_by"])
            locked.save(update_fields=update_fields)
            record_audit(
                f"plan.{target}",
                entity=locked,
                before=before,
                after=snapshot(locked),
                actor=actor,
                request=request,
            )
    return locked


def complete_item(
    item: PlanItem,
    *,
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> PlanItem:
    """Mark one item completed and its linked tooth state in one transaction."""

    with transaction.atomic():
        with with_clinic(item.clinic):
            plan = TreatmentPlan.all_objects.select_for_update().get(pk=item.plan_id)
            locked = PlanItem.all_objects.select_for_update().get(pk=item.pk)
            if locked.status == PlanItem.Status.COMPLETED:
                return locked
            if locked.status not in (PlanItem.Status.PROPOSED, PlanItem.Status.ACCEPTED):
                raise ApiError(
                    "invalid_transition",
                    "This item cannot be completed from its current status.",
                    status_code=409,
                    details={"from": locked.status, "to": PlanItem.Status.COMPLETED},
                )
            before = item_snapshot(locked)
            locked.status = PlanItem.Status.COMPLETED
            locked.save(update_fields=["status", "updated_at"])

            tooth_before: dict[str, Any] | None = None
            if locked.tooth_state_id:
                state = ToothState.all_objects.select_for_update().get(
                    pk=locked.tooth_state_id
                )
                tooth_before = {"status": state.status, "version": state.version}
                state.status = ToothState.Status.COMPLETED
                state.plan_item_id = locked.pk
                state.version = state.version + 1
                state.updated_by = actor if getattr(actor, "pk", None) else state.updated_by
                state.save(
                    update_fields=[
                        "status",
                        "plan_item_id",
                        "version",
                        "updated_by",
                        "updated_at",
                    ]
                )

            record_audit(
                "plan.item_completed",
                entity=locked,
                before=before,
                after=item_snapshot(locked),
                actor=actor,
                request=request,
            )
            if tooth_before is not None:
                record_audit(
                    "plan.tooth_state_completed",
                    entity=locked,
                    before=tooth_before,
                    after={"status": ToothState.Status.COMPLETED},
                    actor=actor,
                    request=request,
                )

            # When every item is completed the plan itself is completed too.
            remaining = PlanItem.all_objects.filter(plan_id=plan.pk).exclude(
                status=PlanItem.Status.COMPLETED
            ).count()
            if remaining == 0 and plan.status == TreatmentPlan.Status.ACCEPTED:
                plan_before = snapshot(plan)
                plan.status = TreatmentPlan.Status.COMPLETED
                plan.version = plan.version + 1
                plan.save(update_fields=["status", "version", "updated_at"])
                record_audit(
                    "plan.completed",
                    entity=plan,
                    before=plan_before,
                    after=snapshot(plan),
                    actor=actor,
                    request=request,
                )
    return locked


__all__ = [
    "ACTION_TARGETS",
    "ALLOWED_TRANSITIONS",
    "ITEM_FIELDS",
    "PLAN_EDITABLE_FIELDS",
    "compute_totals",
    "complete_item",
    "create_plan",
    "item_snapshot",
    "items_for",
    "recompute",
    "replace_items",
    "snapshot",
    "transition",
    "update_plan",
]
