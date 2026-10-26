"""Portal self-service actions (T121).

Cancel and reschedule reuse the *same* ``BookingPolicy`` cutoffs the public
booking links use (``apps.booking.services.links.enforce_cutoff``), so a patient
cannot bypass the clinic's window through the portal. A request outside the
policy is a 409 with a human-readable reason.

Every lookup is scoped by both ``clinic_id`` and ``patient_id``, so a foreign
object is an indistinguishable 404.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.booking.services.links import enforce_cutoff
from apps.booking.services.policy import get_policy
from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.plans.models import PlanItem, TreatmentPlan
from apps.scheduling.models import Appointment
from apps.scheduling.services.appointments import cancel as cancel_appointment_service
from apps.scheduling.services.appointments import reschedule as reschedule_appointment_service

ACTION_TARGETS = {
    "accept": PlanItem.Status.ACCEPTED,
    "decline": PlanItem.Status.DECLINED,
}


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", status_code=404)


def _validation(field: str, message: str) -> ApiError:
    return ApiError(
        "validation_error",
        "The request could not be processed.",
        status_code=400,
        field_errors={field: [message]},
    )


def resolve_appointment(clinic_id: Any, patient_id: Any, appointment_id: Any) -> Appointment:
    appointment = (
        Appointment.all_objects.filter(
            clinic_id=clinic_id, patient_id=patient_id, pk=appointment_id
        )
        .select_related("appointment_type", "provider", "location")
        .first()
    )
    if appointment is None:
        raise _not_found()
    return appointment


def resolve_plan(clinic_id: Any, patient_id: Any, plan_id: Any) -> TreatmentPlan:
    plan = TreatmentPlan.all_objects.filter(
        clinic_id=clinic_id, patient_id=patient_id, pk=plan_id
    ).first()
    if plan is None:
        raise _not_found()
    return plan


def cancel_appointment(
    clinic_id: Any,
    patient_id: Any,
    appointment_id: Any,
    *,
    reason: str = "",
    request: Any = None,
) -> Appointment:
    """Cancel the patient's own appointment if the policy window allows it."""

    appointment = resolve_appointment(clinic_id, patient_id, appointment_id)
    policy = get_policy(appointment.clinic)
    enforce_cutoff(appointment, policy.cancel_cutoff_hours, "cancel")

    with transaction.atomic():
        updated = cancel_appointment_service(
            appointment,
            reason=reason or "cancelled_by_patient_portal",
            request=request,
        )
        record_audit(
            "portal.appointment_cancelled",
            entity=updated,
            clinic=updated.clinic_id,
            after={
                "appointment_id": str(updated.id),
                "status": updated.status,
                "cutoff_hours": policy.cancel_cutoff_hours,
            },
            request=request,
        )
    return updated


def reschedule_appointment(
    clinic_id: Any,
    patient_id: Any,
    appointment_id: Any,
    new_start: datetime,
    *,
    request: Any = None,
) -> Appointment:
    """Reschedule the patient's own appointment if online rescheduling is allowed."""

    appointment = resolve_appointment(clinic_id, patient_id, appointment_id)
    policy = get_policy(appointment.clinic)
    if not policy.allow_online_reschedule:
        raise ApiError(
            "policy_cutoff",
            "Online rescheduling is disabled for this clinic.",
            status_code=409,
        )
    if new_start is None:
        raise _validation("start_at", "this field is required")
    if timezone.is_naive(new_start):
        raise _validation("start_at", "must include a timezone offset")

    enforce_cutoff(appointment, policy.reschedule_cutoff_hours, "reschedule")

    with transaction.atomic():
        new = reschedule_appointment_service(
            appointment,
            new_start,
            reason="rescheduled_by_patient_portal",
            request=request,
        )
        record_audit(
            "portal.appointment_rescheduled",
            entity=new,
            clinic=new.clinic_id,
            before={"appointment_id": str(appointment.id)},
            after={
                "appointment_id": str(new.id),
                "rescheduled_from": str(appointment.id),
                "start_at": new.start_at.isoformat(),
                "cutoff_hours": policy.reschedule_cutoff_hours,
            },
            request=request,
        )
    return new


def set_plan_item_status(
    clinic_id: Any,
    patient_id: Any,
    plan_id: Any,
    item_id: Any,
    action: str,
    *,
    request: Any = None,
) -> PlanItem:
    """Accept or decline one plan item on the patient's own plan."""

    target = ACTION_TARGETS.get(str(action))
    if target is None:
        raise _not_found()

    plan = resolve_plan(clinic_id, patient_id, plan_id)
    item = PlanItem.all_objects.filter(clinic_id=clinic_id, plan=plan, pk=item_id).first()
    if item is None:
        raise _not_found()

    with transaction.atomic():
        if item.status == target:
            return item
        if item.status != PlanItem.Status.PROPOSED:
            raise ApiError(
                "invalid_transition",
                "This item cannot be changed from its current status.",
                status_code=409,
                details={"from": item.status, "to": target},
            )
        before = {"status": item.status}
        item.status = target
        item.save(update_fields=["status", "updated_at"])
        record_audit(
            f"portal.plan_item_{target}",
            entity=item,
            clinic=clinic_id,
            before=before,
            after={"status": item.status, "plan_id": str(plan.id)},
            request=request,
        )
    return item


__all__ = [
    "ACTION_TARGETS",
    "cancel_appointment",
    "reschedule_appointment",
    "resolve_appointment",
    "resolve_plan",
    "set_plan_item_status",
]
