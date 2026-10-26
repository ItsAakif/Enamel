"""Booking orchestration (T112).

This module is the only writer of a public booking. It ties the pieces together
in one transaction so a booking is all-or-nothing:

    validate -> idempotency replay -> slot check + hold -> patient intake ->
    consent capture -> reference -> audit ``booking.created``

The requested ``slot_start`` is validated against the *same* slot engine the
public availability endpoint serves (:func:`public_slots`), so a slot that is
not currently offered can never be booked. Holds are created by
``services.holds`` (which locks the provider row and maps the GiST
``IntegrityError`` to 409), so a concurrent double-book has exactly one winner.

Self-service cancel/reschedule links are also orchestrated here on top of the
``links`` primitives: cutoffs are enforced before the mutation and the token is
consumed exactly once.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.clinics.models import Location
from apps.core.audit import hash_ip
from apps.core.audit import record as record_audit
from apps.core.errors import CODE_SLOT_CONFLICT, ApiError
from apps.core.tenancy import with_clinic
from apps.scheduling.models import Appointment, AppointmentType
from apps.scheduling.services.appointments import cancel as cancel_appointment
from apps.scheduling.services.appointments import reschedule as reschedule_appointment
from apps.scheduling.services.availability import clinic_timezone
from apps.scheduling.status import ACTIVE_STATUSES, STATUS_HELD, STATUS_SCHEDULED

from ..models import BookingReference
from .holds import claim_hold, create_hold, lock_provider
from .intake import record_consents, resolve_patient
from .links import (
    PURPOSE_CANCEL,
    PURPOSE_RESCHEDULE,
    consume_token,
    enforce_cutoff,
    link_max_age_seconds,
    make_token,
    verify_token,
)
from .policy import get_policy
from .slots import public_slots

REFERENCE_PREFIX = "BK-"
REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REFERENCE_LENGTH = 6

logger = logging.getLogger("enamel.booking")


def _validation(field: str, message: str) -> ApiError:
    return ApiError(
        "validation_error",
        "The request could not be processed.",
        status_code=400,
        field_errors={field: [message]},
    )


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", status_code=404)


def _slot_conflict(message: str = "The selected slot is no longer available.") -> ApiError:
    return ApiError(
        CODE_SLOT_CONFLICT,
        message,
        status_code=409,
        field_errors={"start_at": ["this slot is not available"]},
    )


def _hold_expired() -> ApiError:
    return ApiError(
        "hold_expired",
        "The selected slot was held and the hold has expired.",
        status_code=409,
        field_errors={"start_at": ["the previous hold on this slot expired"]},
    )


def _client_ip(request: Any) -> str:
    meta = getattr(request, "META", None)
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("REMOTE_ADDR") or "")


def verification_code(reference: str) -> str:
    """Deterministic 6-digit email-verification code for ``reference``.

    Deriving the code from ``SECRET_KEY`` plus the reference means no extra
    column is needed and the code survives a restart. The reference itself is
    only ever returned to the booker, and the dev backend logs the code so the
    flow is testable without an email adapter (T114 wires real delivery).
    """

    digest = hmac.new(
        str(settings.SECRET_KEY).encode("utf-8"),
        f"booking-verify:{reference}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{int(digest[:8], 16) % 1_000_000:06d}"


def _match_slot(
    clinic: Any,
    appointment_type: AppointmentType,
    location: Location | None,
    slot_start: datetime,
    *,
    provider_id: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the offered slot for ``slot_start`` or raise a 409."""

    local_date = slot_start.astimezone(clinic_timezone(clinic)).date()
    result = public_slots(
        clinic,
        appointment_type,
        local_date,
        local_date,
        location=location,
        now=now,
    )
    for slot in result["slots"]:
        if slot["start"] != slot_start:
            continue
        if provider_id is not None and getattr(slot["provider"], "pk", None) != provider_id:
            continue
        return slot

    # The slot is not offered. Distinguish a stale (not-yet-swept) hold, which
    # the caller can retry after the sweeper runs, from a genuine conflict.
    moment = now or timezone.now()
    slot_end = slot_start + timedelta(minutes=appointment_type.duration_min)
    stale_hold = Appointment.all_objects.filter(
        clinic=clinic,
        status=STATUS_HELD,
        hold_expires_at__isnull=False,
        hold_expires_at__lte=moment,
        start_at__lt=slot_end,
        end_at__gt=slot_start,
    ).exists()
    if stale_hold:
        raise _hold_expired()
    raise _slot_conflict()


def _new_reference(clinic: Any) -> str:
    for _ in range(30):
        candidate = REFERENCE_PREFIX + "".join(
            secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_LENGTH)
        )
        if not BookingReference.all_objects.filter(clinic=clinic, reference=candidate).exists():
            return candidate
    raise ApiError(
        "internal_error",
        "An internal error occurred.",
        status_code=500,
    )


def _enforce_booking_limit(clinic: Any, patient: Any, policy: Any, *, now: datetime | None = None) -> None:
    moment = now or timezone.now()
    limit = int(policy.max_future_bookings)
    if limit <= 0:
        return
    future = Appointment.all_objects.filter(
        clinic=clinic,
        patient_id=patient.pk,
        status__in=sorted(ACTIVE_STATUSES),
        start_at__gt=moment,
    ).count()
    if future >= limit:
        raise ApiError(
            "patient_booking_limit",
            "This patient already has the maximum number of future bookings.",
            status_code=409,
            details={"limit": limit},
        )


def _booking_result(reference: BookingReference) -> dict[str, Any]:
    appointment = reference.appointment
    return {
        "reference": reference.reference,
        "appointment_id": str(appointment.id),
        "starts_at": appointment.start_at.isoformat(),
    }


def _lookup_by_key(clinic: Any, key: str) -> BookingReference | None:
    if not key:
        return None
    return (
        BookingReference.all_objects.filter(clinic=clinic, idempotency_key=key)
        .select_related("appointment")
        .order_by("created_at")
        .first()
    )


def _replay(
    existing: BookingReference,
    appointment_type: AppointmentType,
    slot_start: datetime,
) -> dict[str, Any]:
    """Return the original booking for a repeated idempotency key."""

    if (
        existing.appointment.appointment_type_id != appointment_type.pk
        or existing.appointment.start_at != slot_start
    ):
        raise ApiError(
            "idempotency_mismatch",
            "This idempotency key was already used with a different request.",
            status_code=409,
            details={"reference": existing.reference},
        )
    return _booking_result(existing)


def create_booking(
    *,
    appointment_type: AppointmentType,
    slot_start: datetime,
    patient_data: dict[str, Any],
    consents: dict[str, Any] | None = None,
    location_id: Any = None,
    idempotency_key: str = "",
    request: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a public booking and return ``{reference, appointment_id, starts_at}``."""

    if slot_start is None:
        raise _validation("slot_start", "this field is required")
    if timezone.is_naive(slot_start):
        raise _validation("slot_start", "must include a timezone offset")

    clinic = appointment_type.clinic
    policy = get_policy(clinic)
    if not policy.is_public_enabled:
        raise _not_found()

    location = None
    if location_id:
        location = Location.all_objects.filter(pk=location_id, clinic=clinic).first()
        if location is None:
            raise _validation("location", "location not found for this appointment type")

    moment = now or timezone.now()
    key = (idempotency_key or "").strip()
    ip_hash = hash_ip(_client_ip(request))

    # Idempotency replay runs before the slot check: a repeated key returns the
    # original booking even though its slot is now taken.
    existing = _lookup_by_key(clinic, key)
    if existing is not None:
        return _replay(existing, appointment_type, slot_start)

    try:
        matched = _match_slot(clinic, appointment_type, location, slot_start, now=moment)
    except ApiError as exc:
        # A concurrent request with the same key may have created the booking
        # between the replay lookup above and the slot check.
        existing = _lookup_by_key(clinic, key)
        if existing is not None and getattr(exc, "status_code", None) == 409:
            return _replay(existing, appointment_type, slot_start)
        raise

    provider = matched.get("provider")
    if provider is None:
        raise _slot_conflict("No provider is available for this slot.")

    with transaction.atomic():
        with with_clinic(clinic):
            lock_provider(clinic, provider)

            existing = _lookup_by_key(clinic, key)
            if existing is not None:
                return _replay(existing, appointment_type, slot_start)

            patient, _created = resolve_patient(clinic, **patient_data)
            _enforce_booking_limit(clinic, patient, policy, now=moment)

            hold = create_hold(
                clinic,
                provider=provider,
                appointment_type=appointment_type,
                location=matched["location"],
                operatory=matched.get("operatory"),
                patient=patient,
                slot_start=slot_start,
                phone_e164=patient.phone_e164,
                idempotency_key=key,
                request=request,
                now=moment,
            )
            appointment = claim_hold(hold, target_status=STATUS_SCHEDULED, request=request)

            reference = BookingReference.all_objects.create(
                clinic=clinic,
                reference=_new_reference(clinic),
                appointment=appointment,
                patient=patient,
                source=BookingReference.Source.ONLINE,
                idempotency_key=key,
            )
            record_consents(
                clinic,
                patient=patient,
                reference=reference,
                consents=consents,
                wording_version=policy.consent_wording_version,
                ip_hash=ip_hash,
                captured_at=moment,
            )
            record_audit(
                "booking.created",
                entity=reference,
                after={
                    "reference": reference.reference,
                    "appointment_id": str(appointment.id),
                    "appointment_type_id": str(appointment_type.pk),
                    "provider_id": str(provider.pk),
                    "location_id": str(appointment.location_id),
                    "start_at": appointment.start_at.isoformat(),
                    "status": appointment.status,
                    "source": reference.source,
                    "consents": sorted(str(purpose) for purpose in (consents or {})),
                },
                request=request,
            )

    if policy.require_email_verification:
        logger.info(
            "booking_verification_code",
            extra={
                "reference": reference.reference,
                "code": verification_code(reference.reference),
            },
        )

    return _booking_result(reference)


def verify_booking(reference_value: str, code: str, *, request: Any = None) -> BookingReference:
    """Verify the email code for a booking (no-op when policy does not require it)."""

    reference = (
        BookingReference.all_objects.filter(reference=reference_value)
        .select_related("appointment")
        .order_by("created_at")
        .first()
    )
    if reference is None:
        raise _not_found()

    policy = get_policy(reference.clinic)
    if not policy.require_email_verification:
        return reference

    expected = verification_code(reference.reference)
    provided = str(code or "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise ApiError(
            "invalid_code",
            "That verification code is not correct.",
            status_code=400,
            field_errors={"code": ["is not correct"]},
        )

    with transaction.atomic():
        with with_clinic(reference.clinic):
            reference.email_verified_at = timezone.now()
            reference.save(update_fields=["email_verified_at", "updated_at"])
            record_audit(
                "booking.email_verified",
                entity=reference,
                after={"reference": reference.reference},
                request=request,
            )
    return reference


def link_tokens_for(appointment: Appointment) -> dict[str, str]:
    """Issue cancel/reschedule tokens for ``appointment`` (used by tests/UI)."""

    policy = get_policy(appointment.clinic)
    max_age = link_max_age_seconds(policy)
    return {
        PURPOSE_CANCEL: make_token(appointment, PURPOSE_CANCEL, max_age_seconds=max_age),
        PURPOSE_RESCHEDULE: make_token(
            appointment, PURPOSE_RESCHEDULE, max_age_seconds=max_age
        ),
    }


def _mask_name(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    return cleaned[0] + "*" * max(len(cleaned) - 1, 1)


def _mask_phone(value: str) -> str:
    digits = "".join(char for char in (value or "") if char.isdigit())
    if len(digits) < 2:
        return "*" * len(digits)
    return "*" * (len(digits) - 2) + digits[-2:]


def link_summary(token: str) -> dict[str, Any]:
    """Return a masked appointment summary for a signed link (no ownership leak)."""

    appointment, payload = verify_token(token)
    policy = get_policy(appointment.clinic)
    reference = (
        BookingReference.all_objects.filter(appointment=appointment)
        .order_by("created_at")
        .first()
    )
    purpose = payload.get("p")
    allowed: list[str] = []
    if purpose == PURPOSE_CANCEL:
        allowed = ["cancel"]
    elif purpose == PURPOSE_RESCHEDULE and policy.allow_online_reschedule:
        allowed = ["reschedule"]

    patient = appointment.patient
    return {
        "reference": reference.reference if reference is not None else None,
        "appointment_id": str(appointment.id),
        "status": appointment.status,
        "starts_at": appointment.start_at.isoformat(),
        "ends_at": appointment.end_at.isoformat(),
        "appointment_type": appointment.appointment_type.name,
        "provider_name": appointment.provider.display_name,
        "location_name": appointment.location.name,
        "patient": {
            "first_name": _mask_name(patient.first_name),
            "last_name": _mask_name(patient.last_name),
            "phone": _mask_phone(patient.phone_e164),
        },
        "allowed_actions": allowed,
        "cancel_cutoff_hours": policy.cancel_cutoff_hours,
        "reschedule_cutoff_hours": policy.reschedule_cutoff_hours,
    }


def cancel_via_link(token: str, *, request: Any = None, now: datetime | None = None) -> dict[str, Any]:
    """Cancel the appointment behind a cancel link (single use, cutoff enforced)."""

    appointment, _payload = verify_token(token, purpose=PURPOSE_CANCEL)
    policy = get_policy(appointment.clinic)
    enforce_cutoff(appointment, policy.cancel_cutoff_hours, "cancel", now=now)

    with transaction.atomic():
        consume_token(token, purpose=PURPOSE_CANCEL, appointment=appointment)
        with with_clinic(appointment.clinic):
            updated = cancel_appointment(
                appointment, reason="cancelled_by_patient_link", request=request
            )
            record_audit(
                "booking.cancelled",
                entity=updated,
                after={
                    "appointment_id": str(updated.id),
                    "status": updated.status,
                    "reason": updated.cancellation_reason,
                },
                request=request,
            )
    return {
        "appointment_id": str(updated.id),
        "status": updated.status,
        "starts_at": updated.start_at.isoformat(),
    }


def reschedule_via_link(
    token: str,
    new_start: datetime,
    *,
    request: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reschedule the appointment behind a reschedule link (single use)."""

    appointment, _payload = verify_token(token, purpose=PURPOSE_RESCHEDULE)
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

    moment = now or timezone.now()
    enforce_cutoff(appointment, policy.reschedule_cutoff_hours, "reschedule", now=moment)

    matched = _match_slot(
        appointment.clinic,
        appointment.appointment_type,
        appointment.location,
        new_start,
        provider_id=appointment.provider_id,
        now=moment,
    )

    with transaction.atomic():
        consume_token(token, purpose=PURPOSE_RESCHEDULE, appointment=appointment)
        with with_clinic(appointment.clinic):
            new = reschedule_appointment(
                appointment,
                new_start,
                operatory=matched.get("operatory") or appointment.operatory,
                reason="rescheduled_by_patient_link",
                request=request,
            )
            record_audit(
                "booking.rescheduled",
                entity=new,
                before={"appointment_id": str(appointment.id)},
                after={
                    "appointment_id": str(new.id),
                    "rescheduled_from": str(appointment.id),
                    "start_at": new.start_at.isoformat(),
                    "status": new.status,
                },
                request=request,
            )
    return {
        "appointment_id": str(new.id),
        "rescheduled_from": str(appointment.id),
        "status": new.status,
        "starts_at": new.start_at.isoformat(),
    }


__all__ = [
    "REFERENCE_PREFIX",
    "cancel_via_link",
    "create_booking",
    "link_summary",
    "link_tokens_for",
    "reschedule_via_link",
    "verification_code",
    "verify_booking",
]
