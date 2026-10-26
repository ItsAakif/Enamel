"""Suppression list and opt-out rules (T114).

Exact rule (documented and tested)
----------------------------------
A suppression row is keyed by ``(clinic, channel, address_hash)``. A suppressed
address blocks every purpose **except** :attr:`Consent.Purpose.TRANSACTIONAL`:

* ``appointment_reminders``, ``recall`` and ``marketing`` are blocked;
* ``transactional`` (a confirmation for something the patient just did) is
  still allowed.

A STOP/HELP/bounce/complaint event is patient-centric: it suppresses *all*
channels the patient has an address for (email plus SMS/WhatsApp/voice on the
same phone number), because an opt-out is about the person, not the pipe. The
insert is idempotent through the unique constraint, so duplicate provider events
never create a second row.

The address is always stored as a salted SHA-256 hash (the salt is the app
``SECRET_KEY``); the raw email/phone never reaches the database.
"""

from __future__ import annotations

import hashlib
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.core.tenancy import with_clinic

from ..models import Consent, Suppression

ALL_CHANNELS = (
    Consent.Channel.EMAIL,
    Consent.Channel.SMS,
    Consent.Channel.WHATSAPP,
    Consent.Channel.VOICE,
)

#: Purposes a suppression row blocks. ``transactional`` is deliberately exempt.
BLOCKED_PURPOSES = frozenset(
    {
        Consent.Purpose.APPOINTMENT_REMINDERS,
        Consent.Purpose.RECALL,
        Consent.Purpose.MARKETING,
    }
)


def purpose_is_blocked(purpose: str) -> bool:
    """True when a suppressed address must not receive ``purpose``."""

    return purpose in BLOCKED_PURPOSES


def address_for(patient: Any, channel: str) -> str:
    """Return the raw address a channel would use for ``patient`` (may be "")."""

    if channel == Consent.Channel.EMAIL:
        return str(getattr(patient, "email", "") or "").strip()
    if channel in (Consent.Channel.SMS, Consent.Channel.WHATSAPP, Consent.Channel.VOICE):
        return str(getattr(patient, "phone_e164", "") or "").strip()
    return ""


def address_hash(channel: str, address: str) -> str:
    """Return a salted SHA-256 of ``address`` (empty string when no address)."""

    cleaned = (address or "").strip()
    if not cleaned:
        return ""
    salt = str(getattr(settings, "SECRET_KEY", "") or "")
    return hashlib.sha256(f"{salt}:{channel}:{cleaned}".encode()).hexdigest()


def add(clinic: Any, channel: str, digest: str, reason: str) -> Suppression | None:
    """Insert a suppression row, idempotently. No address means nothing to add."""

    if not digest:
        return None
    with transaction.atomic():
        with with_clinic(clinic):
            try:
                row, _ = Suppression.all_objects.get_or_create(
                    clinic=clinic,
                    channel=channel,
                    address_hash=digest,
                    defaults={"reason": reason},
                )
            except IntegrityError:
                row = Suppression.all_objects.filter(
                    clinic=clinic, channel=channel, address_hash=digest
                ).first()
        return row


def is_suppressed(clinic: Any, channel: str, digest: str) -> bool:
    """True when the hashed address is on the clinic's suppression list."""

    if not digest:
        return False
    return Suppression.all_objects.filter(
        clinic=clinic, channel=channel, address_hash=digest
    ).exists()


def record_opt_out(
    patient: Any,
    *,
    channel: str,
    reason: str,
) -> list[Suppression]:
    """Suppress every channel the patient has an address for (idempotent).

    ``reason`` is one of ``stop``/``help``/``bounce``/``complaint``/``manual``.
    Returns the rows that exist after the call.
    """

    clinic = patient.clinic
    rows: list[Suppression] = []
    with transaction.atomic():
        with with_clinic(clinic):
            for candidate in ALL_CHANNELS:
                digest = address_hash(candidate, address_for(patient, candidate))
                row = add(clinic, candidate, digest, reason)
                if row is not None:
                    rows.append(row)
    return rows


__all__ = [
    "ALL_CHANNELS",
    "BLOCKED_PURPOSES",
    "add",
    "address_for",
    "address_hash",
    "is_suppressed",
    "purpose_is_blocked",
    "record_opt_out",
]
