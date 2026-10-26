"""Duplicate patient detection and phone normalization (T106).

Duplicate detection answers one question for the create endpoint: "is this
person probably already in *this* clinic?" It never looks at another clinic.
Three signals are used, in the order the API documents them:

* ``phone``        -- exact match on the normalized E.164 value
* ``email``        -- case-insensitive exact match
* ``name_and_dob`` -- last name (case-insensitive) plus date of birth

A candidate is reported once with every reason that matched. The result is
bounded (``limit``) and ordered by the match order above so responses are
deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import phonenumbers

from apps.core.tenancy import with_clinic

from ..models import Patient

DEFAULT_REGION = "IN"
REASON_PHONE = "phone"
REASON_EMAIL = "email"
REASON_NAME_AND_DOB = "name_and_dob"
REASON_ORDER = (REASON_PHONE, REASON_EMAIL, REASON_NAME_AND_DOB)
MAX_CANDIDATES = 5


class InvalidPhoneNumber(ValueError):
    """Raised when a submitted phone number cannot be parsed as E.164."""


def normalize_phone(raw: str | None, *, region: str = DEFAULT_REGION) -> str:
    """Return ``raw`` as an E.164 string, or ``""`` when it is empty.

    Raises :class:`InvalidPhoneNumber` for anything that is not a valid number
    for ``region`` (or for the number's own country when it starts with ``+``).
    The caller turns that into a field error.
    """

    value = (raw or "").strip()
    if not value:
        return ""
    try:
        parsed = phonenumbers.parse(value, region)
    except phonenumbers.NumberParseException as exc:
        raise InvalidPhoneNumber("enter a valid phone number") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise InvalidPhoneNumber("enter a valid phone number")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def phone_search_digits(raw: str | None) -> str:
    """Return the digits of ``raw`` (normalized when possible)."""

    value = (raw or "").strip()
    if not value:
        return ""
    try:
        normalized = normalize_phone(value)
    except InvalidPhoneNumber:
        normalized = ""
    if normalized:
        return normalized.lstrip("+")
    return "".join(char for char in value if char.isdigit())


@dataclass(frozen=True)
class DuplicateCandidate:
    """One probable duplicate, with the reasons it matched."""

    id: str
    number: str
    name: str
    reasons: tuple[str, ...]

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "number": self.number,
            "name": self.name,
            "reasons": list(self.reasons),
        }


def find_duplicates(
    clinic: Any,
    *,
    first_name: str = "",
    last_name: str = "",
    date_of_birth: Any = None,
    phone_e164: str = "",
    email: str = "",
    exclude_patient_id: Any = None,
    limit: int = MAX_CANDIDATES,
) -> list[DuplicateCandidate]:
    """Return probable duplicates for the given demographics, in ``clinic``."""

    reasons: dict[Any, set[str]] = {}

    with with_clinic(clinic):
        base = Patient.objects.filter(deleted_at__isnull=True, merged_into__isnull=True)
        if exclude_patient_id is not None:
            base = base.exclude(pk=exclude_patient_id)

        probes: list[tuple[str, dict[str, Any]]] = []
        if phone_e164:
            probes.append((REASON_PHONE, {"phone_e164": phone_e164}))
        if email:
            probes.append((REASON_EMAIL, {"email__iexact": email}))
        if last_name and date_of_birth:
            probes.append(
                (
                    REASON_NAME_AND_DOB,
                    {"last_name__iexact": last_name, "date_of_birth": date_of_birth},
                )
            )

        for reason, lookup in probes:
            for patient_id in base.filter(**lookup).values_list("id", flat=True)[:limit]:
                reasons.setdefault(patient_id, set()).add(reason)

        if not reasons:
            return []

        patients = {
            patient.id: patient
            for patient in Patient.objects.filter(id__in=list(reasons))
            .only("id", "number", "first_name", "last_name")
        }

    candidates: list[DuplicateCandidate] = []
    for patient_id, matched in reasons.items():
        patient = patients.get(patient_id)
        if patient is None:
            continue
        ordered = tuple(reason for reason in REASON_ORDER if reason in matched)
        candidates.append(
            DuplicateCandidate(
                id=str(patient.id),
                number=patient.number,
                name=patient.full_name,
                reasons=ordered,
            )
        )
    candidates.sort(key=lambda candidate: (len(candidate.reasons) * -1, candidate.number))
    return candidates[:limit]


__all__ = [
    "DEFAULT_REGION",
    "MAX_CANDIDATES",
    "REASON_EMAIL",
    "REASON_NAME_AND_DOB",
    "REASON_ORDER",
    "REASON_PHONE",
    "DuplicateCandidate",
    "InvalidPhoneNumber",
    "find_duplicates",
    "normalize_phone",
    "phone_search_digits",
]
