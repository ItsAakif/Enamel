"""Patient intake for public booking (T112).

Two responsibilities:

1. **Dedup or provision.** A returning patient is matched by normalized phone
   *and* date of birth inside the booking clinic; anything else becomes a
   provisional record tagged ``source=online``. Matching never looks at another
   clinic and never returns the matched record to the caller -- only the newly
   created ``BookingReference`` is exposed.
2. **Consent capture.** Every consent purpose submitted with the booking is
   stored as a ``BookingConsent`` row with the wording version and timestamp, so
   the clinic can prove what was agreed and when.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.utils import timezone

from apps.core.errors import ApiError
from apps.core.tenancy import with_clinic
from apps.patients.models import Patient
from apps.patients.services.dedup import InvalidPhoneNumber, normalize_phone
from apps.patients.services.numbering import allocate_patient_number

from ..models import BookingConsent, BookingReference

MAX_NAME_LENGTH = 128


def _validation(field: str, message: str) -> ApiError:
    return ApiError(
        "validation_error",
        "The request could not be processed.",
        status_code=400,
        field_errors={field: [message]},
    )


def _clean_name(value: Any, field: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise _validation(f"patient.{field}", "this field is required")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise _validation(f"patient.{field}", "is too long")
    return cleaned


def resolve_patient(
    clinic: Any,
    *,
    first_name: Any,
    last_name: Any,
    phone: Any,
    date_of_birth: Any = None,
    email: Any = "",
    notes: Any = "",
) -> tuple[Patient, bool]:
    """Return ``(patient, created)`` for the submitted demographics.

    Raises a 400 field error for a missing name or an unparseable phone. An
    existing record is matched on ``phone_e164`` plus ``date_of_birth``; a
    partial match (shared phone, different DOB) provisions a new patient.
    """

    first = _clean_name(first_name, "first_name")
    last = _clean_name(last_name, "last_name")

    try:
        phone_e164 = normalize_phone(str(phone or ""))
    except InvalidPhoneNumber as exc:
        raise _validation("patient.phone", str(exc)) from exc
    if not phone_e164:
        raise _validation("patient.phone", "this field is required")

    dob = date_of_birth
    if isinstance(dob, str):
        from django.utils.dateparse import parse_date

        dob = parse_date(dob)
    if dob is not None and isinstance(dob, datetime):
        dob = dob.date()

    with with_clinic(clinic):
        if dob is not None:
            match = (
                Patient.all_objects.filter(
                    clinic=clinic,
                    phone_e164=phone_e164,
                    date_of_birth=dob,
                    deleted_at__isnull=True,
                    merged_into__isnull=True,
                )
                .order_by("created_at", "id")
                .first()
            )
            if match is not None:
                return match, False

        number = allocate_patient_number(clinic)
        patient = Patient.objects.create(
            clinic=clinic,
            number=number,
            first_name=first,
            last_name=last,
            date_of_birth=dob,
            phone_e164=phone_e164,
            email=str(email or "").strip().lower(),
            notes=str(notes or ""),
            source=Patient.Source.ONLINE,
        )
    return patient, True


def record_consents(
    clinic: Any,
    *,
    patient: Patient,
    reference: BookingReference | None,
    consents: dict[str, Any] | None,
    wording_version: str,
    ip_hash: str = "",
    captured_at: datetime | None = None,
) -> list[BookingConsent]:
    """Persist one ``BookingConsent`` row per submitted purpose."""

    if not consents:
        return []

    valid = set(BookingConsent.Purpose.values)
    unknown = sorted(str(key) for key in consents if str(key) not in valid)
    if unknown:
        raise _validation(
            "consents",
            f"unknown consent purpose(s): {', '.join(unknown)}",
        )

    moment = captured_at or timezone.now()
    rows: list[BookingConsent] = []
    with with_clinic(clinic):
        for purpose, granted in consents.items():
            rows.append(
                BookingConsent.all_objects.create(
                    clinic=clinic,
                    patient=patient,
                    reference=reference,
                    purpose=str(purpose),
                    wording_version=wording_version,
                    granted=bool(granted),
                    captured_at=moment,
                    ip_hash=ip_hash,
                )
            )
    return rows


__all__ = ["record_consents", "resolve_patient"]
