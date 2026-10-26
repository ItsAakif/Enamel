"""Per-clinic patient numbering (T106).

``Patient.number`` is ``EN-`` plus a zero-padded 6 digit sequence that restarts
for every clinic. The sequence is stored in ``patients_patientcounter`` and is
handed out under a ``SELECT ... FOR UPDATE`` row lock so two concurrent creates
in the same clinic cannot receive the same value.

The counter row is created separately from the lock: ``get_or_create`` runs in
its own savepoint, so a lost create race raises ``IntegrityError``, is retried
by Django and the row is seen afterwards. Only once the row exists (committed
or owned by another, already-committed transaction) is it locked.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from apps.core.tenancy import with_clinic

from ..models import PatientCounter

PATIENT_NUMBER_PREFIX = "EN-"
PATIENT_NUMBER_WIDTH = 6


def format_patient_number(value: int) -> str:
    """Return ``1`` -> ``EN-000001``."""

    if value < 1:
        raise ValueError("Patient numbers start at 1.")
    return f"{PATIENT_NUMBER_PREFIX}{value:0{PATIENT_NUMBER_WIDTH}d}"


def _ensure_counter(clinic: Any) -> None:
    PatientCounter.objects.get_or_create(clinic=clinic)


def reserve_patient_number(clinic: Any) -> int:
    """Reserve and return the next integer for ``clinic`` (row locked)."""

    # ``get_or_create`` must be able to commit its insert before the lock below
    # is taken, otherwise two transactions could each insert the row.
    # The counter row is addressed by the clinic, so the write and the lock
    # both run inside the clinic's context (ADR-013).
    with with_clinic(clinic):
        _ensure_counter(clinic)
        with transaction.atomic():
            counter = PatientCounter.objects.select_for_update().get(clinic=clinic)
            reserved = counter.next_number
            counter.next_number = reserved + 1
            counter.save(update_fields=["next_number", "updated_at"])
    return reserved


def allocate_patient_number(clinic: Any) -> str:
    """Return the next formatted patient number for ``clinic``."""

    return format_patient_number(reserve_patient_number(clinic))


__all__ = [
    "PATIENT_NUMBER_PREFIX",
    "PATIENT_NUMBER_WIDTH",
    "allocate_patient_number",
    "format_patient_number",
    "reserve_patient_number",
]
