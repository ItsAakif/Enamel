"""Signed self-service links (T112).

Cancel and reschedule links are HMAC-signed with ``django.core.signing`` and
carry the appointment id, the clinic id, a purpose (``cancel``/``reschedule``)
and an absolute expiry epoch. Verification fails closed:

* a tampered or truncated token is 410 ``invalid_token``;
* an expired token is 410 ``expired``;
* a token whose purpose does not match the requested action is 410
  ``invalid_token``.

A mutation also inserts a :class:`~apps.booking.models.SignedLinkUse` row keyed
by the token's SHA-256, so the same link can never cancel or reschedule twice:
the unique constraint (and an explicit pre-check) turns a replay into 410
``invalid_token``. The raw token is never stored.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from django.core import signing
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.errors import ApiError
from apps.core.tenancy import with_clinic
from apps.scheduling.models import Appointment

from ..models import SignedLinkUse

SALT = "enamel.booking.links"
PURPOSE_CANCEL = SignedLinkUse.Purpose.CANCEL
PURPOSE_RESCHEDULE = SignedLinkUse.Purpose.RESCHEDULE
VALID_PURPOSES = frozenset({PURPOSE_CANCEL, PURPOSE_RESCHEDULE})


def invalid_token() -> ApiError:
    return ApiError(
        "invalid_token",
        "This link is not valid.",
        status_code=410,
    )


def expired_token() -> ApiError:
    return ApiError(
        "expired",
        "This link has expired.",
        status_code=410,
    )


def link_max_age_seconds(policy: Any) -> int:
    """Token lifetime derived from the clinic policy (at least one day)."""

    days = max(int(getattr(policy, "window_days", 31) or 31), 1)
    return days * 86400


def make_token(appointment: Appointment, purpose: str, *, max_age_seconds: int) -> str:
    """Return a signed, expiring token for ``appointment`` and ``purpose``."""

    if purpose not in VALID_PURPOSES:
        raise ValueError(f"unknown link purpose: {purpose!r}")
    payload = {
        "a": str(appointment.pk),
        "c": str(appointment.clinic_id),
        "p": purpose,
        "e": int(timezone.now().timestamp()) + int(max_age_seconds),
    }
    return signing.dumps(payload, salt=SALT, compress=True)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load_token(token: str) -> dict[str, Any]:
    """Return the token payload or raise 410 ``invalid_token``."""

    try:
        payload = signing.loads(token, salt=SALT)
    except signing.BadSignature:
        raise invalid_token() from None
    if not isinstance(payload, dict) or "a" not in payload or "c" not in payload:
        raise invalid_token()
    return payload


def verify_token(token: str, *, purpose: str | None = None) -> tuple[Appointment, dict[str, Any]]:
    """Validate ``token`` and return ``(appointment, payload)``."""

    payload = load_token(token)
    if purpose is not None and payload.get("p") != purpose:
        raise invalid_token()
    expires_at = int(payload.get("e", 0) or 0)
    if expires_at <= int(timezone.now().timestamp()):
        raise expired_token()

    appointment = Appointment.all_objects.filter(
        pk=payload.get("a"), clinic_id=payload.get("c")
    ).first()
    if appointment is None:
        raise invalid_token()
    return appointment, payload


def consume_token(token: str, *, purpose: str, appointment: Appointment) -> None:
    """Record a single use of ``token``; a replay raises 410 ``invalid_token``."""

    digest = token_hash(token)
    if SignedLinkUse.all_objects.filter(token_hash=digest).exists():
        raise invalid_token()
    try:
        with transaction.atomic():
            with with_clinic(appointment.clinic):
                SignedLinkUse.all_objects.create(
                    clinic=appointment.clinic,
                    token_hash=digest,
                    purpose=purpose,
                    appointment=appointment,
                )
    except IntegrityError as exc:
        raise invalid_token() from exc


def is_used(token: str) -> bool:
    return SignedLinkUse.all_objects.filter(token_hash=token_hash(token)).exists()


def cutoff_error(action: str, hours: int) -> ApiError:
    past_tense = {"cancel": "cancelled", "reschedule": "rescheduled"}.get(action, action)
    return ApiError(
        "policy_cutoff",
        f"This appointment can no longer be {past_tense} online.",
        status_code=409,
        field_errors={action: [f"the {hours}h policy cutoff has passed"]},
        details={"cutoff_hours": hours},
    )


def enforce_cutoff(
    appointment: Appointment,
    hours: int,
    action: str,
    *,
    now: datetime | None = None,
) -> None:
    """Raise 409 ``policy_cutoff`` when ``appointment`` is inside the window."""

    moment = now or timezone.now()
    if appointment.start_at - moment < timedelta(hours=int(hours)):
        raise cutoff_error(action, int(hours))


__all__ = [
    "PURPOSE_CANCEL",
    "PURPOSE_RESCHEDULE",
    "SALT",
    "VALID_PURPOSES",
    "consume_token",
    "cutoff_error",
    "enforce_cutoff",
    "expired_token",
    "invalid_token",
    "is_used",
    "link_max_age_seconds",
    "load_token",
    "make_token",
    "token_hash",
    "verify_token",
]
