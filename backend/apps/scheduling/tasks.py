"""Celery beat tasks for scheduling (T116).

The only scheduled entry is the waitlist offer sweeper: every 60 s it expires
offers past ``expires_at``, cancels their held appointments and returns the
entries to the pool. It is a thin wrapper over
``services.offers.expire_offers`` so the beat path and the unit-test path
cannot diverge.

Like the booking hold sweeper, it runs as the **worker** role, which holds no
bypass: it iterates the clinic catalogue (outside the row-level-security scope
by design) and runs each clinic's cleanup inside that clinic's context, so
every read and every write is scoped rather than privileged (ADR-013, decision
D-2).

One clinic whose data is inconsistent must not stop the sweep: the failure is
counted and logged, and the remaining clinics are still swept.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from apps.core.tenancy import with_clinic

from .services.offers import expire_offers as expire_offers_service

logger = logging.getLogger("enamel.scheduling")

TASK_EXPIRE_WAITLIST_OFFERS = "scheduling.tasks.expire_waitlist_offers"


@shared_task(name=TASK_EXPIRE_WAITLIST_OFFERS)
def expire_waitlist_offers() -> dict[str, Any]:
    """Every 60 s: release expired waitlist offers across every clinic."""

    from apps.clinics.models import Clinic

    expired = 0
    clinics = 0
    errors = 0
    for clinic in Clinic.objects.all().iterator():
        clinics += 1
        try:
            with with_clinic(clinic):
                expired += int(expire_offers_service(clinic=clinic).get("expired", 0))
        except Exception:  # noqa: BLE001 - one clinic must not stop the sweep
            errors += 1
            logger.exception(
                "scheduling.expire_waitlist_offers.clinic_failed",
                extra={"clinic_id": str(clinic.id)},
            )
            continue
    return {"clinics": clinics, "expired": expired, "errors": errors}


__all__ = ["TASK_EXPIRE_WAITLIST_OFFERS", "expire_waitlist_offers"]
