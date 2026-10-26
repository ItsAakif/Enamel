"""Reservation holds (T112).

A hold is a real ``scheduling.Appointment`` in ``status=held`` (with
``hold_expires_at``) plus a ``ReservationHold`` row. Creating one reuses the
scheduling service so the provider lock, the savepoint that turns a GiST
``IntegrityError`` into a 409 and the ``appointment.created`` audit row are all
shared with staff bookings -- there is no second slot engine.

The expiry sweeper (:func:`expire_holds`) cancels stale held appointments (via
``scheduling.services.appointments.expire_holds``) and marks the matching
``ReservationHold`` rows ``expired``, returning the slot to the pool.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.clinics.models import Provider
from apps.core.errors import CODE_HOLD_EXPIRED, CODE_SLOT_CONFLICT, ApiError
from apps.core.tenancy import with_clinic
from apps.scheduling.models import Appointment, Operatory
from apps.scheduling.services.appointments import (
    create_appointment,
    find_conflict,
    transition,
)
from apps.scheduling.services.appointments import (
    expire_holds as scheduling_expire_holds,
)
from apps.scheduling.status import STATUS_HELD, STATUS_SCHEDULED

from ..models import ReservationHold
from .policy import get_policy


def lock_provider(clinic: Any, provider: Provider) -> None:
    """Serialise slot claims for one provider (released on commit)."""

    Provider.all_objects.select_for_update().filter(clinic=clinic, pk=provider.pk).first()


def _validation(field: str, message: str) -> ApiError:
    return ApiError(
        "validation_error",
        "The request could not be processed.",
        status_code=400,
        field_errors={field: [message]},
    )


def _slot_conflict(conflict: Appointment | None) -> ApiError:
    details: dict[str, Any] = {}
    if conflict is not None:
        details = {
            "appointment_id": str(conflict.id),
            "provider_id": str(conflict.provider_id),
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


def _hold_expired() -> ApiError:
    return ApiError(
        CODE_HOLD_EXPIRED,
        "The selected slot was held and the hold has expired.",
        status_code=409,
        field_errors={"start_at": ["the previous hold on this slot expired"]},
    )


def _conflict_error(conflict: Appointment | None, now: datetime) -> ApiError:
    """Map an overlapping appointment to ``hold_expired`` or ``slot_conflict``."""

    if (
        conflict is not None
        and conflict.status == STATUS_HELD
        and conflict.hold_expires_at is not None
        and conflict.hold_expires_at <= now
    ):
        return _hold_expired()
    return _slot_conflict(conflict)


def create_hold(
    clinic: Any,
    *,
    provider: Provider,
    appointment_type: Any,
    location: Any,
    patient: Any,
    slot_start: datetime,
    operatory: Operatory | None = None,
    phone_e164: str = "",
    token_hash: str = "",
    idempotency_key: str = "",
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> ReservationHold:
    """Claim one slot as a held appointment and return the ``ReservationHold``.

    The provider row is locked before the slot is checked, so two concurrent
    claims for the same provider serialise and the loser gets a 409. A hold is
    time-boxed: a **live** hold still blocks the slot (409
    ``slot_conflict``), an **elapsed** one does not - the public availability
    offers that slot again, so the booking takes it (N-02). The stale hold is
    released by the sweeper.
    """

    if slot_start is None:
        raise _validation("start_at", "this field is required")
    if timezone.is_naive(slot_start):
        raise _validation("start_at", "must include a timezone offset")

    moment = now or timezone.now()
    slot_end = slot_start + timedelta(minutes=appointment_type.duration_min)
    policy = get_policy(clinic)
    key = (idempotency_key or "").strip()

    with transaction.atomic():
        with with_clinic(clinic):
            lock_provider(clinic, provider)

            conflict = find_conflict(
                clinic,
                provider_id=provider.pk,
                operatory_id=operatory.pk if operatory is not None else None,
                start_at=slot_start,
                end_at=slot_end,
            )
            if conflict is not None:
                raise _conflict_error(conflict, moment)

            appointment = create_appointment(
                clinic,
                patient=patient,
                provider=provider,
                appointment_type=appointment_type,
                location=location,
                start_at=slot_start,
                end_at=slot_end,
                operatory=operatory,
                source=Appointment.Source.ONLINE,
                idempotency_key=key,
                status=STATUS_HELD,
                actor=actor,
                request=request,
            )

            expires_at = moment + timedelta(minutes=policy.hold_minutes)
            appointment.hold_expires_at = expires_at
            appointment.save(update_fields=["hold_expires_at", "updated_at"])

            hold = ReservationHold.all_objects.create(
                clinic=clinic,
                appointment=appointment,
                provider=provider,
                operatory=operatory,
                slot_start=slot_start,
                slot_end=slot_end,
                token_hash=token_hash,
                phone_e164=phone_e164,
                status=ReservationHold.Status.ACTIVE,
                idempotency_key=key,
                expires_at=expires_at,
            )
    return hold


def claim_hold(
    hold: ReservationHold,
    *,
    target_status: str = STATUS_SCHEDULED,
    actor: Any = None,
    request: Any = None,
) -> Appointment:
    """Convert a hold into a real appointment (idempotent).

    A second claim of the same hold returns the appointment unchanged. A hold
    whose appointment already expired (or was cancelled) raises 409
    ``hold_expired``.
    """

    appointment = hold.appointment
    now = timezone.now()

    if hold.status == ReservationHold.Status.CLAIMED:
        return appointment
    if hold.status == ReservationHold.Status.EXPIRED:
        raise _hold_expired()
    if appointment.status != STATUS_HELD:
        hold.status = ReservationHold.Status.EXPIRED
        hold.save(update_fields=["status", "updated_at"])
        raise _hold_expired()
    if appointment.hold_expires_at is not None and appointment.hold_expires_at <= now:
        hold.status = ReservationHold.Status.EXPIRED
        hold.save(update_fields=["status", "updated_at"])
        raise _hold_expired()

    appointment = transition(
        appointment, target_status, actor=actor, request=request
    )
    hold.status = ReservationHold.Status.CLAIMED
    hold.save(update_fields=["status", "updated_at"])
    return appointment


def expire_holds(*, now: datetime | None = None, clinic: Any = None) -> dict[str, int]:
    """Cancel stale held appointments and mark their holds ``expired``.

    Returns ``{"released": <appointments cancelled>, "expired_holds": <rows>}``.
    Safe to run repeatedly: only ``held`` appointments past their expiry and
    ``active`` holds past theirs are touched.

    The read and the write are scoped to ``clinic`` when one is given, so the
    sweeper needs no maintenance bypass (ADR-013, decision D-2).
    """

    from apps.core.tenancy import with_clinic

    moment = now or timezone.now()
    scope = with_clinic(clinic) if clinic is not None else nullcontext()
    with scope:
        released = scheduling_expire_holds(now=moment, clinic=clinic)

        holds = ReservationHold.all_objects.filter(
            status=ReservationHold.Status.ACTIVE,
            expires_at__lte=moment,
        )
        if clinic is not None:
            holds = holds.filter(clinic_id=getattr(clinic, "id", clinic))
        expired = holds.update(status=ReservationHold.Status.EXPIRED)
    return {"released": released, "expired_holds": expired}


def release_hold(hold: ReservationHold) -> None:
    """Mark a hold expired without touching its appointment (best effort)."""

    if hold.status != ReservationHold.Status.EXPIRED:
        hold.status = ReservationHold.Status.EXPIRED
        hold.save(update_fields=["status", "updated_at"])


__all__ = [
    "CODE_HOLD_EXPIRED",
    "claim_hold",
    "create_hold",
    "expire_holds",
    "lock_provider",
    "release_hold",
]
