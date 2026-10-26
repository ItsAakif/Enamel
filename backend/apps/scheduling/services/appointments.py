"""Appointment lifecycle services (T110).

This module is the only legal writer of ``Appointment`` and ``AppointmentEvent``.
It owns four invariants:

1. **One transaction per change.** The appointment row, its
   ``AppointmentEvent`` and its ``core.audit`` row are written together; a failed
   audit rolls the appointment change back. An illegal transition writes
   nothing at all.
2. **The slot is claimed under a lock.** The provider row is locked with
   ``SELECT ... FOR UPDATE`` before the insert, so concurrent bookings of the
   same provider serialise and the loser hits the GiST exclusion constraint
   instead of creating a second row.
3. **A constraint violation is a 409, never a 500.** The insert runs inside a
   savepoint; ``IntegrityError`` from the exclusion constraint is translated to
   ``slot_conflict`` with the conflicting appointment id, and the outer
   transaction still rolls back cleanly.
4. **Holds expire.** ``status=held`` carries ``hold_expires_at`` and is released
   by :func:`expire_holds`, which cancels stale holds and frees the slot.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.clinics.models import Provider
from apps.core.audit import record as record_audit
from apps.core.errors import (
    CODE_IDEMPOTENCY_MISMATCH,
    CODE_SLOT_CONFLICT,
    CODE_VERSION_CONFLICT,
    ApiError,
)
from apps.core.tenancy import with_clinic

from ..models import Appointment, AppointmentEvent, AppointmentType
from ..status import (
    ACTIVE_STATUSES,
    ALL_STATUSES,
    DEFAULT_HOLD_MINUTES,
    STATUS_CANCELLED,
    STATUS_HELD,
    STATUS_SCHEDULED,
    can_transition,
)

#: Fields a PATCH may change through :func:`update_appointment`.
UPDATABLE_FIELDS = ("reason", "notes", "operatory_id", "appointment_type_id")


def _validation(field: str, message: str) -> ApiError:
    return ApiError(
        "validation_error",
        "The request could not be processed.",
        status_code=400,
        field_errors={field: [message]},
    )


def _invalid_transition(appointment: Appointment, to_status: str) -> ApiError:
    return ApiError(
        "invalid_transition",
        "This appointment cannot move to the requested status.",
        status_code=409,
        field_errors={"status": [f"cannot transition from {appointment.status} to {to_status}"]},
        details={
            "appointment_id": str(appointment.id),
            "from": appointment.status,
            "to": to_status,
        },
    )


def _slot_conflict(conflict: Appointment | None) -> ApiError:
    details: dict[str, Any] = {}
    if conflict is not None:
        details = {
            "appointment_id": str(conflict.id),
            "provider_id": str(conflict.provider_id),
            "operatory_id": str(conflict.operatory_id) if conflict.operatory_id else None,
            "start_at": conflict.start_at.isoformat(),
            "end_at": conflict.end_at.isoformat(),
        }
    return ApiError(
        CODE_SLOT_CONFLICT,
        "The selected slot is no longer available.",
        status_code=409,
        field_errors={"start_at": ["conflicts with an existing appointment"]},
        details=details,
    )


def snapshot(appointment: Appointment) -> dict[str, Any]:
    """Return the audit/event projection of an appointment (ids, never free text)."""

    return {
        "status": appointment.status,
        "start_at": appointment.start_at.isoformat(),
        "end_at": appointment.end_at.isoformat(),
        "provider_id": str(appointment.provider_id),
        "operatory_id": str(appointment.operatory_id) if appointment.operatory_id else None,
        "location_id": str(appointment.location_id),
        "appointment_type_id": str(appointment.appointment_type_id),
        "version": appointment.version,
    }


def hold_minutes(clinic: Any) -> int:
    settings = getattr(clinic, "settings_json", None) or {}
    policy = settings.get("booking_policy") or {}
    try:
        value = int(policy.get("hold_minutes", DEFAULT_HOLD_MINUTES))
    except (TypeError, ValueError):
        value = DEFAULT_HOLD_MINUTES
    return value if value > 0 else DEFAULT_HOLD_MINUTES


def _require_aware(value: datetime, field: str) -> datetime:
    if value is None:
        raise _validation(field, "this field is required")
    if timezone.is_naive(value):
        raise _validation(field, "must include a timezone offset")
    return value


def _resolve_end(appointment_type: Any, start_at: datetime, end_at: datetime | None) -> datetime:
    expected = timedelta(minutes=appointment_type.duration_min)
    if end_at is None:
        return start_at + expected
    _require_aware(end_at, "end_at")
    if end_at <= start_at:
        raise _validation("end_at", "must be after start_at")
    if end_at - start_at != expected:
        raise _validation(
            "end_at",
            f"must equal the appointment type duration ({appointment_type.duration_min} minutes)",
        )
    return end_at


def _assert_same_clinic(clinic: Any, **objects: Any) -> None:
    for name, obj in objects.items():
        if obj is None:
            continue
        if getattr(obj, "clinic_id", None) != clinic.id:
            raise _validation(name, "not found in this clinic")


def _lock_provider(clinic: Any, provider: Provider) -> None:
    """Serialise slot claims for one provider (released on commit)."""

    Provider.all_objects.select_for_update().filter(clinic=clinic, pk=provider.pk).first()


def find_conflict(
    clinic: Any,
    *,
    provider_id: Any,
    operatory_id: Any,
    start_at: datetime,
    end_at: datetime,
    now: datetime | None = None,
) -> Appointment | None:
    """Return an active appointment that overlaps the range (provider first).

    A hold is time-boxed, so the rule is the one the public availability uses:
    a **live** hold (``hold_expires_at`` in the future) occupies the slot, an
    **elapsed** hold does not. The exclusion constraints leave the holds out
    for the same reason (``statuses.EXCLUSION_STATUSES``): a GiST predicate
    cannot be time-aware, so the two rules would disagree for the window
    between the expiry and the sweeper.
    """

    moment = now or timezone.now()
    overlapping = Appointment.all_objects.filter(
        clinic=clinic,
        status__in=sorted(ACTIVE_STATUSES),
        start_at__lt=end_at,
        end_at__gt=start_at,
    ).exclude(
        status=STATUS_HELD,
        hold_expires_at__isnull=False,
        hold_expires_at__lte=moment,
    )
    match = overlapping.filter(provider_id=provider_id).order_by("start_at").first()
    if match is not None:
        return match
    if operatory_id:
        return overlapping.filter(operatory_id=operatory_id).order_by("start_at").first()
    return None


def _record_event(
    appointment: Appointment,
    event_type: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    actor: Any,
    clinic: Any,
) -> AppointmentEvent:
    return AppointmentEvent.objects.create(
        clinic=clinic,
        appointment=appointment,
        event_type=event_type,
        before=before,
        after=after,
        actor=actor,
    )


def create_appointment(
    clinic: Any,
    *,
    patient: Any,
    provider: Provider,
    appointment_type: Any,
    location: Any,
    start_at: datetime,
    end_at: datetime | None = None,
    operatory: Any = None,
    source: str | None = None,
    reason: str = "",
    notes: str = "",
    idempotency_key: str = "",
    status: str | None = None,
    actor: Any = None,
    request: Any = None,
) -> Appointment:
    """Create an appointment, claiming the slot under a provider lock."""

    _require_aware(start_at, "start_at")
    target_status = status or STATUS_SCHEDULED
    if target_status not in ALL_STATUSES:
        raise _validation("status", "not a valid appointment status")
    end_at = _resolve_end(appointment_type, start_at, end_at)
    _assert_same_clinic(
        clinic,
        patient=patient,
        provider=provider,
        location=location,
        appointment_type=appointment_type,
        operatory=operatory,
    )
    key = (idempotency_key or "").strip()

    with transaction.atomic():
        with with_clinic(clinic):
            if key:
                existing = (
                    Appointment.all_objects.filter(clinic=clinic, idempotency_key=key)
                    .order_by("created_at")
                    .first()
                )
                if existing is not None:
                    if (
                        existing.patient_id == patient.pk
                        and existing.provider_id == provider.pk
                        and existing.start_at == start_at
                    ):
                        return existing
                    raise ApiError(
                        CODE_IDEMPOTENCY_MISMATCH,
                        "This idempotency key was already used with a different request.",
                        status_code=409,
                        details={"appointment_id": str(existing.id)},
                    )

            _lock_provider(clinic, provider)

            # The application is the rule for the slot. The exclusion
            # constraints are the backstop for the statuses they cover
            # (``statuses.EXCLUSION_STATUSES``), but they cannot express the
            # hold expiry - a GiST predicate may not use ``now()``. The check
            # runs under the provider lock, so the check-then-insert is
            # serialised for the provider.
            conflict = find_conflict(
                clinic,
                provider_id=provider.pk,
                operatory_id=operatory.pk if operatory is not None else None,
                start_at=start_at,
                end_at=end_at,
            )
            if conflict is not None:
                raise _slot_conflict(conflict)

            appointment = Appointment(
                clinic=clinic,
                location=location,
                patient=patient,
                provider=provider,
                operatory=operatory,
                appointment_type=appointment_type,
                start_at=start_at,
                end_at=end_at,
                status=target_status,
                source=source or Appointment.Source.STAFF,
                reason=reason or "",
                notes=notes or "",
                idempotency_key=key,
                created_by=actor,
                version=1,
            )
            if target_status == STATUS_HELD:
                appointment.hold_expires_at = timezone.now() + timedelta(
                    minutes=hold_minutes(clinic)
                )

            try:
                with transaction.atomic():
                    appointment.save()
            except IntegrityError as exc:
                conflict = find_conflict(
                    clinic,
                    provider_id=provider.pk,
                    operatory_id=operatory.pk if operatory is not None else None,
                    start_at=start_at,
                    end_at=end_at,
                )
                raise _slot_conflict(conflict) from exc

            after = snapshot(appointment)
            _record_event(appointment, AppointmentEvent.EventType.CREATED, None, after, actor, clinic)
            record_audit(
                "appointment.created",
                entity=appointment,
                after=after,
                actor=actor,
                request=request,
            )
    return appointment


def update_appointment(
    appointment: Appointment,
    *,
    fields: dict[str, Any],
    version: int | None = None,
    actor: Any = None,
    request: Any = None,
) -> Appointment:
    """Apply ``fields`` to ``appointment`` and audit the change."""

    if version is not None and int(version) != appointment.version:
        raise ApiError(
            CODE_VERSION_CONFLICT,
            "This appointment was modified by someone else.",
            status_code=409,
            field_errors={"version": ["stale version"]},
            details={"current": snapshot(appointment)},
        )

    changes = {name: value for name, value in fields.items() if name in UPDATABLE_FIELDS}
    if not changes:
        return appointment

    clinic = appointment.clinic
    new_type = changes.get("appointment_type_id")
    if new_type is not None:
        resolved = AppointmentType.all_objects.filter(clinic=clinic, pk=new_type).first()
        if resolved is None:
            raise _validation("appointment_type", "not found in this clinic")
        expected = timedelta(minutes=resolved.duration_min)
        if appointment.end_at - appointment.start_at != expected:
            raise _validation(
                "appointment_type",
                "the new type duration does not match the appointment length",
            )

    with transaction.atomic():
        with with_clinic(clinic):
            before = snapshot(appointment)
            for name, value in changes.items():
                setattr(appointment, name, value)
            appointment.version = appointment.version + 1
            appointment.save(update_fields=[*changes, "version", "updated_at"])
            after = snapshot(appointment)
            _record_event(
                appointment, AppointmentEvent.EventType.UPDATED, before, after, actor, clinic
            )
            record_audit(
                "appointment.updated",
                entity=appointment,
                before=before,
                after=after,
                actor=actor,
                request=request,
            )
    return appointment


def transition(
    appointment: Appointment,
    to_status: str,
    *,
    actor: Any = None,
    request: Any = None,
    cancellation_reason: str = "",
) -> Appointment:
    """Move an appointment to ``to_status`` (legal transitions only)."""

    if to_status not in ALL_STATUSES:
        raise _validation("status", "not a valid appointment status")
    if not can_transition(appointment.status, to_status):
        # Illegal transitions must write nothing: raise before the transaction.
        raise _invalid_transition(appointment, to_status)

    clinic = appointment.clinic
    with transaction.atomic():
        with with_clinic(clinic):
            before = snapshot(appointment)
            appointment.status = to_status
            update_fields = ["status", "version", "updated_at"]
            if to_status == STATUS_CANCELLED:
                appointment.cancellation_reason = (cancellation_reason or "")[:255]
                update_fields.append("cancellation_reason")
            if to_status == STATUS_HELD:
                appointment.hold_expires_at = timezone.now() + timedelta(
                    minutes=hold_minutes(clinic)
                )
                update_fields.append("hold_expires_at")
            appointment.version = appointment.version + 1
            appointment.save(update_fields=update_fields)
            after = snapshot(appointment)

            cancelled = to_status == STATUS_CANCELLED
            _record_event(
                appointment,
                AppointmentEvent.EventType.CANCELLED
                if cancelled
                else AppointmentEvent.EventType.STATUS_CHANGED,
                before,
                after,
                actor,
                clinic,
            )
            record_audit(
                "appointment.cancelled" if cancelled else "appointment.status_changed",
                entity=appointment,
                before=before,
                after=after,
                actor=actor,
                request=request,
            )
    return appointment


def cancel(
    appointment: Appointment,
    *,
    reason: str = "",
    actor: Any = None,
    request: Any = None,
) -> Appointment:
    """Cancel an appointment with a reason."""

    return transition(
        appointment,
        STATUS_CANCELLED,
        actor=actor,
        request=request,
        cancellation_reason=reason,
    )


def reschedule(
    appointment: Appointment,
    new_start: datetime,
    *,
    operatory: Any = None,
    reason: str = "",
    actor: Any = None,
    request: Any = None,
) -> Appointment:
    """Cancel ``appointment`` and create its successor in one transaction.

    The new row points back through ``rescheduled_from`` and starts at version 1;
    the cancelled row keeps its own history. Both writes share one transaction,
    so a slot conflict on the new time rolls the cancellation back too.
    """

    _require_aware(new_start, "start_at")
    appointment_type = appointment.appointment_type
    new_end = new_start + timedelta(minutes=appointment_type.duration_min)
    clinic = appointment.clinic
    chosen_operatory = operatory if operatory is not None else appointment.operatory
    if operatory is not None:
        _assert_same_clinic(clinic, operatory=operatory)

    with transaction.atomic():
        with with_clinic(clinic):
            _lock_provider(clinic, appointment.provider)

            before_old = snapshot(appointment)
            appointment.status = STATUS_CANCELLED
            appointment.cancellation_reason = "rescheduled"
            appointment.version = appointment.version + 1
            appointment.save(
                update_fields=["status", "cancellation_reason", "version", "updated_at"]
            )
            after_old = snapshot(appointment)
            _record_event(
                appointment,
                AppointmentEvent.EventType.CANCELLED,
                before_old,
                after_old,
                actor,
                clinic,
            )

            new = Appointment(
                clinic=clinic,
                location=appointment.location,
                patient=appointment.patient,
                provider=appointment.provider,
                operatory=chosen_operatory,
                appointment_type=appointment_type,
                start_at=new_start,
                end_at=new_end,
                status=STATUS_SCHEDULED,
                source=appointment.source,
                reason=reason or appointment.reason,
                notes=appointment.notes,
                rescheduled_from=appointment,
                created_by=actor,
                version=1,
            )
            try:
                with transaction.atomic():
                    new.save()
            except IntegrityError as exc:
                conflict = find_conflict(
                    clinic,
                    provider_id=appointment.provider_id,
                    operatory_id=chosen_operatory.pk if chosen_operatory else None,
                    start_at=new_start,
                    end_at=new_end,
                )
                raise _slot_conflict(conflict) from exc

            after_new = snapshot(new)
            _record_event(new, AppointmentEvent.EventType.CREATED, None, after_new, actor, clinic)
            _record_event(
                appointment,
                AppointmentEvent.EventType.RESCHEDULED,
                before_old,
                {"rescheduled_to": str(new.id), "start_at": new_start.isoformat()},
                actor,
                clinic,
            )
            record_audit(
                "appointment.rescheduled",
                entity=new,
                before={
                    "appointment_id": str(appointment.id),
                    "start_at": before_old["start_at"],
                },
                after={**after_new, "rescheduled_from": str(appointment.id)},
                actor=actor,
                request=request,
            )
    return new


def expire_holds(*, now: datetime | None = None, clinic: Any = None) -> int:
    """Cancel every held appointment whose hold has elapsed; return the count.

    The read and the writes are scoped to ``clinic`` when one is given, so the
    sweeper needs no maintenance bypass (ADR-013, decision D-2).
    """

    from apps.core.tenancy import with_clinic

    moment = now or timezone.now()
    scope = with_clinic(clinic) if clinic is not None else nullcontext()
    with scope:
        queryset = Appointment.all_objects.filter(
            status=STATUS_HELD,
            hold_expires_at__isnull=False,
            hold_expires_at__lte=moment,
        )
        if clinic is not None:
            queryset = queryset.filter(clinic_id=getattr(clinic, "id", clinic))

        released = 0
        for appointment in list(queryset):
            transition(
                appointment,
                STATUS_CANCELLED,
                cancellation_reason="hold_expired",
            )
            released += 1
    return released


__all__ = [
    "UPDATABLE_FIELDS",
    "cancel",
    "create_appointment",
    "expire_holds",
    "find_conflict",
    "hold_minutes",
    "reschedule",
    "snapshot",
    "transition",
    "update_appointment",
]
