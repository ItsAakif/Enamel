"""Portal one-time-code flow (T121).

A code is 6 digits, expires after ``PORTAL_OTP_TTL_MINUTES`` (10), and allows at
most ``PORTAL_OTP_MAX_ATTEMPTS`` (5) wrong tries before the challenge is
invalidated. Neither the code nor the contact is stored: both are kept as salted
SHA-256 hashes.

Requesting a code for a contact that matches no patient returns ``None`` from
:func:`request_code`; the API still returns the same generic 202 with an opaque
(random) challenge id, so the response cannot be used to enumerate patients.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.core.errors import ApiError
from apps.core.rls import set_rls_context
from apps.patients.models import Patient

from ..adapters.otp import get_adapter
from ..models import PortalChannel, PortalOtpChallenge
from . import identity as identity_service

OTP_LENGTH = 6
DEFAULT_TTL_MINUTES = 10
DEFAULT_MAX_ATTEMPTS = 5
PHONE_CHANNEL_REGION = "IN"


def _salt() -> str:
    return str(getattr(settings, "SECRET_KEY", "") or "enamel-portal-salt")


def _digest(value: str) -> str:
    return hashlib.sha256(f"{_salt()}:{value}".encode()).hexdigest()


def otp_ttl_minutes() -> int:
    return int(getattr(settings, "PORTAL_OTP_TTL_MINUTES", DEFAULT_TTL_MINUTES))


def max_attempts() -> int:
    return int(getattr(settings, "PORTAL_OTP_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS))


def generate_code() -> str:
    return f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"


def hash_code(code: str) -> str:
    return _digest(f"code:{str(code).strip()}")


def hash_contact(contact: str) -> str:
    return _digest(f"contact:{str(contact).strip().lower()}")


def normalize_contact(raw: str) -> tuple[str, str]:
    """Return ``(channel, normalized_contact)`` for a phone or email string."""

    value = str(raw or "").strip()
    if "@" in value:
        return PortalChannel.EMAIL, value.lower()
    from apps.patients.services.dedup import InvalidPhoneNumber, normalize_phone

    try:
        normalized = normalize_phone(value, region=PHONE_CHANNEL_REGION)
    except InvalidPhoneNumber:
        normalized = ""
    if normalized:
        return PortalChannel.SMS, normalized
    digits = "".join(char for char in value if char.isdigit() or char == "+")
    return PortalChannel.SMS, digits


def find_patient(clinic: Any, channel: str, normalized: str) -> Patient | None:
    if not normalized:
        return None
    queryset = Patient.all_objects.filter(clinic=clinic, deleted_at__isnull=True)
    if channel == PortalChannel.EMAIL:
        return queryset.filter(email__iexact=normalized).first()
    return queryset.filter(phone_e164=normalized).first()


def request_code_with_code(
    clinic: Any, contact: str, *, request: Any = None
) -> tuple[PortalOtpChallenge, str] | tuple[None, str]:
    """Create and deliver a challenge; return ``(challenge, code)``.

    The code is returned in the clear, so this variant must only be called by
    call sites that have decided the code may be revealed (``DEMO_MODE`` -
    the guarded demo deployment - or the DEBUG echo in ``PortalRequestCodeView``).
    """

    channel, normalized = normalize_contact(contact)
    # The clinic and the presented contact are the capabilities that resolve
    # the patient; both are transaction-local and never stored.
    set_rls_context(clinic_id=clinic.id, portal_contact=normalized)
    patient = find_patient(clinic, channel, normalized)
    if patient is None:
        return None, ""

    set_rls_context(portal_patient_id=patient.id)
    code = generate_code()
    now = timezone.now()
    challenge = PortalOtpChallenge.objects.create(
        patient=patient,
        clinic_id=clinic.id,
        channel=channel,
        contact_hash=hash_contact(normalized),
        code_hash=hash_code(code),
        expires_at=now + timedelta(minutes=otp_ttl_minutes()),
        attempts=0,
        max_attempts=max_attempts(),
    )
    get_adapter().send(channel=channel, contact=normalized, code=code)
    return challenge, code


def request_code(clinic: Any, contact: str, *, request: Any = None) -> PortalOtpChallenge | None:
    """Create and deliver a challenge, or None when the contact is unknown.

    The bootstrap runs under the minimal portal-auth role (T137): it may read
    the clinic, resolve the patient behind the presented contact inside that
    clinic and write the challenge row. It may not read a clinical row.
    """

    challenge, _ = request_code_with_code(clinic, contact, request=request)
    return challenge


def _invalid_otp() -> ApiError:
    return ApiError(
        "invalid_otp",
        "That code is not correct.",
        status_code=400,
        field_errors={"code": ["is not correct"]},
    )


def invalidate(challenge: PortalOtpChallenge, *, now: Any = None) -> PortalOtpChallenge:
    set_rls_context(
        clinic_id=challenge.clinic_id,
        portal_patient_id=challenge.patient_id,
    )
    challenge.used_at = now or timezone.now()
    challenge.save(update_fields=["used_at"])
    return challenge


def verify_code(challenge_id: Any, code: str, *, request: Any = None) -> tuple[Any, str]:
    """Verify a code and return ``(session, raw_key)`` or raise 400 ``invalid_otp``."""

    # The presented challenge id is the capability that resolves the row; a
    # guessed id matches nothing.
    set_rls_context(portal_challenge_id=challenge_id)
    challenge = PortalOtpChallenge.objects.filter(pk=challenge_id).first()
    if challenge is None:
        raise _invalid_otp()
    set_rls_context(
        clinic_id=challenge.clinic_id,
        portal_patient_id=challenge.patient_id,
    )
    now = timezone.now()
    if challenge.used_at is not None or challenge.expires_at <= now:
        raise _invalid_otp()

    if not hmac.compare_digest(hash_code(code), challenge.code_hash):
        challenge.attempts = challenge.attempts + 1
        fields = ["attempts"]
        if challenge.attempts >= challenge.max_attempts:
            challenge.used_at = now
            fields.append("used_at")
        challenge.save(update_fields=fields)
        raise _invalid_otp()

    challenge.used_at = now
    challenge.save(update_fields=["used_at"])
    patient = Patient.all_objects.filter(pk=challenge.patient_id).first()
    if patient is None:
        raise _invalid_otp()
    return identity_service.create_session(patient, request=request)


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_TTL_MINUTES",
    "OTP_LENGTH",
    "find_patient",
    "generate_code",
    "hash_code",
    "hash_contact",
    "invalidate",
    "max_attempts",
    "normalize_contact",
    "otp_ttl_minutes",
    "request_code",
    "request_code_with_code",
    "verify_code",
]
