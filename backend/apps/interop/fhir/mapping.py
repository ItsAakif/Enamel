"""Internal model -> FHIR R4 projection (T130).

The projection is deliberately narrow and lossless in the direction that matters:
the API is read-only, so only the fields a downstream system may safely see are
mapped. Clinical free text (encounter ``notes``, appointment ``notes``) is *not*
projected: it is neither part of a FHIR ``Appointment``/``Encounter`` core field
here nor appropriate for an unauthenticated-of-purpose integration payload.

Identifiers
-----------
Every resource carries an ``identifier`` under the ``urn:enamel:<type>`` system so
a consumer can round-trip back to the internal UUID without guessing.
"""

from __future__ import annotations

from typing import Any

PATIENT_IDENTIFIER_SYSTEM = "urn:enamel:patient"
APPOINTMENT_IDENTIFIER_SYSTEM = "urn:enamel:appointment"
ENCOUNTER_IDENTIFIER_SYSTEM = "urn:enamel:encounter"
PROVIDER_IDENTIFIER_SYSTEM = "urn:enamel:provider"
CLINIC_IDENTIFIER_SYSTEM = "urn:enamel:clinic"

#: Enamel appointment status -> FHIR R4 ``Appointment.status``.
APPOINTMENT_STATUS_MAP: dict[str, str] = {
    "requested": "proposed",
    "held": "pending",
    "scheduled": "booked",
    "confirmed": "booked",
    "checked_in": "checked-in",
    "in_progress": "arrived",
    "completed": "fulfilled",
    "cancelled": "cancelled",
    "no_show": "noshow",
}

#: Enamel encounter status -> FHIR R4 ``Encounter.status``.
ENCOUNTER_STATUS_MAP: dict[str, str] = {
    "draft": "planned",
    "finalized": "finished",
    "amended": "finished",
}

#: Enamel ``Patient.sex_at_birth`` -> FHIR ``Patient.gender``.
GENDER_MAP: dict[str, str] = {
    "female": "female",
    "male": "male",
    "intersex": "other",
    "unknown": "unknown",
}

AMBULATORY_CLASS = {
    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
    "code": "AMB",
    "display": "ambulatory",
}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def _identifier(system: str, value: Any) -> dict[str, Any]:
    return {"system": system, "value": str(value)}


def _telecom(patient: Any) -> list[dict[str, Any]]:
    contacts: list[dict[str, Any]] = []
    phone = str(getattr(patient, "phone_e164", "") or "").strip()
    if phone:
        contacts.append({"system": "phone", "value": phone, "use": "mobile"})
    email = str(getattr(patient, "email", "") or "").strip()
    if email:
        contacts.append({"system": "email", "value": email})
    return contacts


def _address(patient: Any) -> list[dict[str, Any]]:
    raw = getattr(patient, "address_json", None) or {}
    if not isinstance(raw, dict) or not raw:
        return []
    address: dict[str, Any] = {}
    line = raw.get("line1") or raw.get("line") or raw.get("street")
    if line:
        address["line"] = [str(line)]
    if raw.get("line2"):
        address.setdefault("line", []).append(str(raw["line2"]))
    for key, target in (("city", "city"), ("state", "state"), ("postal_code", "postalCode")):
        if raw.get(key):
            address[target] = str(raw[key])
    if raw.get("country"):
        address["country"] = str(raw["country"])
    return [address] if address else []


def patient_to_fhir(patient: Any) -> dict[str, Any]:
    """Project a ``patients.Patient`` to a FHIR R4 Patient dict."""

    resource: dict[str, Any] = {
        "resourceType": "Patient",
        "id": str(patient.pk),
        "identifier": [
            _identifier(PATIENT_IDENTIFIER_SYSTEM, patient.number),
            _identifier(CLINIC_IDENTIFIER_SYSTEM, patient.clinic_id),
        ],
        "active": getattr(patient, "status", "active") == "active",
    }

    given = [str(getattr(patient, "first_name", "") or "").strip()]
    preferred = str(getattr(patient, "preferred_name", "") or "").strip()
    name: dict[str, Any] = {"family": str(getattr(patient, "last_name", "") or "").strip()}
    if preferred and preferred not in given:
        given.append(preferred)
    name["given"] = [part for part in given if part]
    if name["family"] or name["given"]:
        resource["name"] = [name]

    birth_date = _iso(getattr(patient, "date_of_birth", None))
    if birth_date:
        resource["birthDate"] = birth_date

    gender = GENDER_MAP.get(str(getattr(patient, "sex_at_birth", "") or "unknown"), "unknown")
    resource["gender"] = gender

    telecom = _telecom(patient)
    if telecom:
        resource["telecom"] = telecom

    address = _address(patient)
    if address:
        resource["address"] = address

    return resource


def _appointment_participants(appointment: Any) -> list[dict[str, Any]]:
    participants: list[dict[str, Any]] = []
    patient_id = getattr(appointment, "patient_id", None)
    if patient_id:
        participants.append(
            {
                "actor": {"reference": f"Patient/{patient_id}"},
                "status": "accepted",
            }
        )
    provider = getattr(appointment, "provider", None)
    provider_id = getattr(appointment, "provider_id", None)
    if provider_id:
        actor: dict[str, Any] = {"reference": f"Practitioner/{provider_id}"}
        display = str(getattr(provider, "display_name", "") or "").strip()
        if display:
            actor["display"] = display
        participants.append({"actor": actor, "status": "accepted"})
    return participants


def appointment_to_fhir(appointment: Any) -> dict[str, Any]:
    """Project a ``scheduling.Appointment`` to a FHIR R4 Appointment dict."""

    resource: dict[str, Any] = {
        "resourceType": "Appointment",
        "id": str(appointment.pk),
        "identifier": [
            _identifier(APPOINTMENT_IDENTIFIER_SYSTEM, appointment.pk),
            _identifier(CLINIC_IDENTIFIER_SYSTEM, appointment.clinic_id),
        ],
        "status": APPOINTMENT_STATUS_MAP.get(
            str(getattr(appointment, "status", "") or ""), "pending"
        ),
        "start": _iso(getattr(appointment, "start_at", None)),
        "end": _iso(getattr(appointment, "end_at", None)),
    }

    appointment_type = getattr(appointment, "appointment_type", None)
    if appointment_type is not None:
        coding: dict[str, Any] = {"code": str(getattr(appointment_type, "code", "") or "")}
        display = str(getattr(appointment_type, "name", "") or "").strip()
        if display:
            coding["display"] = display
        # ``Appointment.serviceType`` is 0..* CodeableConcept in R4/R4B.
        resource["serviceType"] = [{"coding": [coding]}]

    participants = _appointment_participants(appointment)
    if participants:
        resource["participant"] = participants

    reason = str(getattr(appointment, "reason", "") or "").strip()
    if reason:
        resource["description"] = reason

    return resource


def encounter_to_fhir(encounter: Any) -> dict[str, Any]:
    """Project a ``patients.Encounter`` to a FHIR R4 Encounter dict."""

    resource: dict[str, Any] = {
        "resourceType": "Encounter",
        "id": str(encounter.pk),
        "identifier": [
            _identifier(ENCOUNTER_IDENTIFIER_SYSTEM, encounter.pk),
            _identifier(CLINIC_IDENTIFIER_SYSTEM, encounter.clinic_id),
        ],
        "status": ENCOUNTER_STATUS_MAP.get(
            str(getattr(encounter, "status", "") or ""), "unknown"
        ),
        "class": dict(AMBULATORY_CLASS),
        "subject": {"reference": f"Patient/{encounter.patient_id}"},
    }

    period = _iso(getattr(encounter, "occurred_at", None))
    if period:
        resource["period"] = {"start": period}

    provider_id = getattr(encounter, "provider_id", None)
    if provider_id:
        resource["participant"] = [
            {"individual": {"reference": f"Practitioner/{provider_id}"}}
        ]

    chief_complaint = str(getattr(encounter, "chief_complaint", "") or "").strip()
    if chief_complaint:
        resource["reasonCode"] = [{"text": chief_complaint}]

    return resource


__all__ = [
    "AMBULATORY_CLASS",
    "APPOINTMENT_IDENTIFIER_SYSTEM",
    "APPOINTMENT_STATUS_MAP",
    "CLINIC_IDENTIFIER_SYSTEM",
    "ENCOUNTER_IDENTIFIER_SYSTEM",
    "ENCOUNTER_STATUS_MAP",
    "GENDER_MAP",
    "PATIENT_IDENTIFIER_SYSTEM",
    "PROVIDER_IDENTIFIER_SYSTEM",
    "appointment_to_fhir",
    "encounter_to_fhir",
    "patient_to_fhir",
]
