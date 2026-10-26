"""Waitlist offers: hold, claim, decline and expiry (T116).

Creating an offer blocks the slot with a real ``held`` appointment, so the
provider/operatory exclusion constraints make a second offer on the same slot
impossible. Claiming promotes the hold to ``scheduled`` and is atomic: the
appointment transition, the offer status and the entry status all commit in one
transaction. Declining or expiring cancels the hold and returns the entry to the
pool. A confirmed appointment is never moved.
"""

from __future__ import annotations

import hashlib
import secrets
from contextlib import nullcontext
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.clinics.models import Location, Provider
from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.tenancy import with_clinic

from ..models import Appointment, Operatory, WaitlistEntry, WaitlistOffer
from ..status import STATUS_CANCELLED, STATUS_HELD, STATUS_SCHEDULED
from .appointments import create_appointment, transition

DEFAULT_TTL_MINUTES = 120
MIN_TTL_MINUTES = 15
MAX_TTL_MINUTES = 240


def _validation(field: str, message: str) -> ApiError:
    return ApiError(
        "validation_error",
        "The request could not be processed.",
        status_code=400,
        field_errors={field: [message]},
    )


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", status_code=404)


def _gone(message: str) -> ApiError:
    return ApiError("offer_unavailable", message, status_code=410)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def snapshot(offer: WaitlistOffer) -> dict[str, Any]:
    return {
        "entry_id": str(offer.entry_id),
        "provider_id": str(offer.provider_id),
        "start_at": offer.start_at.isoformat(),
        "end_at": offer.end_at.isoformat(),
        "status": offer.status,
        "expires_at": offer.expires_at.isoformat(),
    }


def _ttl_minutes(value: int | None) -> int:
    if value is None:
        value = int(getattr(settings, "WAITLIST_OFFER_TTL_MINUTES", DEFAULT_TTL_MINUTES))
    return max(MIN_TTL_MINUTES, min(MAX_TTL_MINUTES, int(value)))


def _resolve_location(clinic: Any, operatory: Operatory | None) -> Location:
    if operatory is not None:
        return operatory.location
    location = (
        Location.all_objects.filter(clinic=clinic, is_active=True)
        .order_by("name", "id")
        .first()
    )
    if location is None:
        raise _validation("location", "this clinic has no active location")
    return location


def create_offer(
    entry: WaitlistEntry,
    *,
    start_at: datetime,
    provider: Provider | None = None,
    operatory: Operatory | None = None,
    ttl_minutes: int | None = None,
    score: dict[str, Any] | None = None,
    now: datetime | None = None,
    actor: Any = None,
    request: Any = None,
) -> tuple[WaitlistOffer, str]:
    """Create an offer, blocking the slot with a held appointment.

    Returns ``(offer, raw_token)``; only the SHA-256 of the token is stored.
    """

    if start_at is None:
        raise _validation("start_at", "this field is required")
    if timezone.is_naive(start_at):
        raise _validation("start_at", "must include a timezone offset")

    clinic = entry.clinic
    appointment_type = entry.appointment_type
    chosen_provider = provider or entry.provider_pref
    if chosen_provider is None:
        raise _validation("provider", "a provider is required to offer a slot")
    if chosen_provider.clinic_id != clinic.id:
        raise _validation("provider", "not found in this clinic")

    moment = now or timezone.now()
    ttl = _ttl_minutes(ttl_minutes)
    end_at = start_at + timedelta(minutes=appointment_type.duration_min)
    location = _resolve_location(clinic, operatory)
    raw_token = secrets.token_urlsafe(32)
    digest = token_hash(raw_token)

    if score is None:
        from .recommend import score_entry

        score = score_entry(
            entry,
            {
                "start": start_at,
                "end": end_at,
                "provider_id": str(chosen_provider.pk),
                "operatory_id": str(operatory.pk) if operatory is not None else None,
                "appointment_type_id": str(appointment_type.pk),
                "reason": "offer",
            },
            now=moment,
        )

    with transaction.atomic():
        with with_clinic(clinic):
            held = create_appointment(
                clinic,
                patient=entry.patient,
                provider=chosen_provider,
                appointment_type=appointment_type,
                location=location,
                start_at=start_at,
                end_at=end_at,
                operatory=operatory,
                source=Appointment.Source.STAFF,
                status=STATUS_HELD,
                actor=actor,
                request=request,
            )
            held.hold_expires_at = moment + timedelta(minutes=ttl)
            held.save(update_fields=["hold_expires_at", "updated_at"])

            offer = WaitlistOffer.all_objects.create(
                clinic=clinic,
                entry=entry,
                start_at=start_at,
                end_at=end_at,
                provider=chosen_provider,
                operatory=operatory,
                token_hash=digest,
                score_json=score or {},
                expires_at=moment + timedelta(minutes=ttl),
                status=WaitlistOffer.Status.OFFERED,
                held_appointment=held,
            )
            entry.status = WaitlistEntry.Status.OFFERED
            entry.save(update_fields=["status", "updated_at"])
            record_audit(
                "waitlist.offer_created",
                entity=offer,
                after=snapshot(offer),
                actor=actor,
                request=request,
            )
    return offer, raw_token


def _release(
    offer: WaitlistOffer,
    *,
    status: str,
    reason: str,
    action: str,
    actor: Any = None,
    request: Any = None,
) -> WaitlistOffer:
    """Cancel the hold, mark the offer, and return the entry to the pool."""

    held = offer.held_appointment
    if held is not None and held.status == STATUS_HELD:
        transition(
            held,
            STATUS_CANCELLED,
            cancellation_reason=reason,
            actor=actor,
            request=request,
        )
    offer.status = status
    offer.held_appointment = None
    offer.save(update_fields=["status", "held_appointment", "updated_at"])

    entry = WaitlistEntry.all_objects.filter(pk=offer.entry_id).first()
    if entry is not None and entry.status == WaitlistEntry.Status.OFFERED:
        entry.status = WaitlistEntry.Status.ACTIVE
        entry.save(update_fields=["status", "updated_at"])

    record_audit(
        action,
        entity=offer,
        after=snapshot(offer),
        actor=actor,
        request=request,
    )
    return offer


def claim_offer(
    token: str,
    *,
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> WaitlistOffer:
    """Claim the offer behind ``token``; a second claim is a 410."""

    digest = token_hash(token or "")
    moment = now or timezone.now()
    with transaction.atomic():
        offer = (
            WaitlistOffer.all_objects.select_for_update()
            .filter(token_hash=digest)
            .first()
        )
        if offer is None:
            raise _not_found()
        with with_clinic(offer.clinic_id):
            if offer.status == WaitlistOffer.Status.CLAIMED:
                raise _gone("This offer has already been claimed.")
            if offer.status in (
                WaitlistOffer.Status.DECLINED,
                WaitlistOffer.Status.EXPIRED,
            ):
                raise _gone("This offer is no longer available.")
            if offer.expires_at <= moment:
                _release(
                    offer,
                    status=WaitlistOffer.Status.EXPIRED,
                    reason="offer_expired",
                    action="waitlist.offer_expired",
                    actor=actor,
                    request=request,
                )
                raise _gone("This offer has expired.")

            held = offer.held_appointment
            if held is None or held.status != STATUS_HELD:
                _release(
                    offer,
                    status=WaitlistOffer.Status.EXPIRED,
                    reason="hold_lost",
                    action="waitlist.offer_expired",
                    actor=actor,
                    request=request,
                )
                raise _gone("This offer is no longer available.")

            appointment = transition(held, STATUS_SCHEDULED, actor=actor, request=request)
            offer.status = WaitlistOffer.Status.CLAIMED
            offer.claimed_appointment = appointment
            offer.save(update_fields=["status", "claimed_appointment", "updated_at"])

            entry = WaitlistEntry.all_objects.filter(pk=offer.entry_id).first()
            if entry is not None:
                entry.status = WaitlistEntry.Status.CLAIMED
                entry.save(update_fields=["status", "updated_at"])

            record_audit(
                "waitlist.offer_claimed",
                entity=offer,
                after={**snapshot(offer), "appointment_id": str(appointment.id)},
                actor=actor,
                request=request,
            )
    return offer


def decline_offer(
    token: str,
    *,
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> WaitlistOffer:
    """Decline the offer behind ``token`` and release the hold."""

    digest = token_hash(token or "")
    with transaction.atomic():
        offer = (
            WaitlistOffer.all_objects.select_for_update()
            .filter(token_hash=digest)
            .first()
        )
        if offer is None:
            raise _not_found()
        with with_clinic(offer.clinic_id):
            if offer.status == WaitlistOffer.Status.DECLINED:
                return offer
            if offer.status in (
                WaitlistOffer.Status.CLAIMED,
                WaitlistOffer.Status.EXPIRED,
            ):
                raise _gone("This offer is no longer available.")
            _release(
                offer,
                status=WaitlistOffer.Status.DECLINED,
                reason="declined",
                action="waitlist.offer_declined",
                actor=actor,
                request=request,
            )
    return offer


def expire_offers(
    *,
    now: datetime | None = None,
    clinic: Any = None,
) -> dict[str, int]:
    """Expire every offered slot past its expiry; release the holds."""

    moment = now or timezone.now()
    scope = with_clinic(clinic) if clinic is not None else nullcontext()
    with scope:
        queryset = WaitlistOffer.all_objects.filter(
            status=WaitlistOffer.Status.OFFERED,
            expires_at__lte=moment,
        )
        if clinic is not None:
            queryset = queryset.filter(clinic_id=getattr(clinic, "id", clinic))

        expired = 0
        for candidate in list(queryset):
            with transaction.atomic():
                with with_clinic(candidate.clinic_id):
                    offer = (
                        WaitlistOffer.all_objects.select_for_update()
                        .filter(pk=candidate.pk, status=WaitlistOffer.Status.OFFERED)
                        .first()
                    )
                    if offer is None:
                        continue
                    _release(
                        offer,
                        status=WaitlistOffer.Status.EXPIRED,
                        reason="offer_expired",
                        action="waitlist.offer_expired",
                    )
                    expired += 1
    return {"expired": expired}


__all__ = [
    "DEFAULT_TTL_MINUTES",
    "MAX_TTL_MINUTES",
    "MIN_TTL_MINUTES",
    "claim_offer",
    "create_offer",
    "decline_offer",
    "expire_offers",
    "snapshot",
    "token_hash",
]
