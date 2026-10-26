"""Public availability (T112).

There is exactly one slot engine in the platform and it lives in
``scheduling.services.availability``. ``public_slots`` is a thin delegation to
:func:`compute_slots` with ``provider=None`` (every bookable provider), so a
public slot list and a staff slot list for the same inputs are byte-identical.
That parity is asserted by ``tests/test_public_slots.py``.

The only thing this module adds is the shape guarantee for anonymous callers:
slots carry provider *display names* only, never an email, phone or membership.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Any

from apps.scheduling.models import AppointmentType
from apps.scheduling.services.availability import compute_slots


def public_slots(
    clinic: Any,
    appointment_type: AppointmentType,
    from_date: date_type,
    to_date: date_type,
    *,
    location: Any = None,
    now: Any = None,
) -> dict[str, Any]:
    """Return ``{"slots": [...], "window": {...}}`` identical to staff output."""

    return compute_slots(
        clinic,
        appointment_type,
        from_date,
        to_date,
        provider=None,
        location=location,
        now=now,
    )


__all__ = ["public_slots"]
