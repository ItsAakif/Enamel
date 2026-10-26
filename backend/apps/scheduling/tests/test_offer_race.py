"""Two concurrent claims on one offer: exactly one winner (T116 acceptance 3)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connections

from apps.core.errors import ApiError
from apps.scheduling.models import Appointment, WaitlistEntry, WaitlistOffer
from apps.scheduling.services import offers as offer_service

from .conftest import utc

pytestmark = pytest.mark.django_db(transaction=True)

WAITLIST = "/api/v1/waitlist/"


def _setup(owner, seed):
    entry = owner.post(
        WAITLIST,
        {
            "patient": str(seed["patient"].id),
            "appointment_type": str(seed["appointment_type"].id),
            "window_start": utc(2026, 1, 5, 9, 0).isoformat(),
            "window_end": utc(2026, 1, 9, 17, 0).isoformat(),
            "urgency": 4,
        },
    ).json()
    created = owner.post(
        f"{WAITLIST}{entry['id']}/offers/",
        {"start_at": utc(2026, 1, 6, 10, 0).isoformat(), "provider": str(seed["provider"].id)},
    ).json()
    return entry, created


def test_concurrent_claims_have_exactly_one_winner(owner, seed_scheduling):
    seed = seed_scheduling()
    entry, created = _setup(owner, seed)
    token = created["token"]
    offer_id = created["id"]

    def claim() -> int:
        connections.close_all()
        try:
            offer_service.claim_offer(token)
            return 200
        except ApiError as exc:
            return exc.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        statuses = sorted(future.result(timeout=30) for future in futures)

    assert statuses == [200, 410]

    offer = WaitlistOffer.all_objects.get(pk=offer_id)
    assert offer.status == WaitlistOffer.Status.CLAIMED
    assert offer.claimed_appointment_id is not None
    assert Appointment.all_objects.filter(clinic=seed["clinic"], status="scheduled").count() == 1
    assert WaitlistEntry.all_objects.get(pk=entry["id"]).status == "claimed"
