"""Celery beat tasks for public booking (T112).

The only scheduled entry is the hold sweeper: every 60 s it cancels held
appointments past ``hold_expires_at`` and marks their ``ReservationHold`` rows
expired, returning the slots to the pool. The function is a thin wrapper over
``services.holds.expire_holds`` so the beat path and the unit-test path cannot
diverge.

The sweeper runs as the **worker** role, which holds no bypass. It therefore
does not need one: it iterates the clinic catalogue (which is outside the
row-level-security scope by design) and runs each clinic's cleanup **inside
that clinic's context**, so every read and every write is scoped rather than
privileged (ADR-013, decision D-2).

One clinic whose data is inconsistent must not stop the sweep: the failure is
counted and logged, and the remaining clinics are still swept.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from apps.core.tenancy import with_clinic

from .services.holds import expire_holds as expire_holds_service

logger = logging.getLogger("enamel.booking")

TASK_EXPIRE_HOLDS = "booking.tasks.expire_holds"


@shared_task(name=TASK_EXPIRE_HOLDS)
def expire_holds() -> dict[str, Any]:
    """Every 60 s: release stale reservation holds across every clinic.

    The sweeper is a cross-clinic job, so it walks the clinic catalogue and
    scopes each clinic's cleanup. It does not use the maintenance bypass: the
    worker role does not hold it, and it must not need it.
    """

    from apps.clinics.models import Clinic

    released = 0
    expired_holds = 0
    clinics = 0
    errors = 0
    for clinic in Clinic.objects.all().iterator():
        clinics += 1
        try:
            with with_clinic(clinic):
                result = expire_holds_service(clinic=clinic)
        except Exception:  # noqa: BLE001 - one clinic must not stop the sweep
            errors += 1
            logger.exception(
                "booking.expire_holds.clinic_failed",
                extra={"clinic_id": str(clinic.id)},
            )
            continue
        released += int(result.get("released", 0))
        expired_holds += int(result.get("expired_holds", 0))
    return {
        "clinics": clinics,
        "released": released,
        "expired_holds": expired_holds,
        "errors": errors,
    }


__all__ = ["TASK_EXPIRE_HOLDS", "expire_holds"]
