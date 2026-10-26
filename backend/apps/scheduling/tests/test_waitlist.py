"""Waitlist registry, gaps and the offer lifecycle (T116 acceptance 1, 2, 4, 5)."""

from __future__ import annotations

from datetime import date

import pytest

from apps.scheduling.models import Appointment, WaitlistEntry, WaitlistOffer
from apps.scheduling.services import offers as offer_service
from apps.scheduling.services.waitlist import detect_gaps

from .conftest import make_appointment, utc

pytestmark = pytest.mark.django_db

WAITLIST = "/api/v1/waitlist/"


def _entry_payload(seed, *, urgency: int = 3) -> dict:
    return {
        "patient": str(seed["patient"].id),
        "appointment_type": str(seed["appointment_type"].id),
        "window_start": utc(2026, 1, 5, 9, 0).isoformat(),
        "window_end": utc(2026, 1, 9, 17, 0).isoformat(),
        "urgency": urgency,
    }


def _create_entry(owner, seed, **kwargs) -> dict:
    response = owner.post(WAITLIST, _entry_payload(seed, **kwargs))
    assert response.status_code == 201, response.json()
    return response.json()


def _create_offer(owner, entry_id, seed, start):
    response = owner.post(
        f"{WAITLIST}{entry_id}/offers/",
        {
            "start_at": start.isoformat(),
            "provider": str(seed["provider"].id),
        },
    )
    return response


def test_cancelled_appointment_creates_a_gap(owner, seed_scheduling):
    seed = seed_scheduling()
    start = utc(2026, 1, 5, 10, 0)
    make_appointment(seed, start, status="cancelled")

    gaps = detect_gaps(seed["clinic"], date(2026, 1, 5), date(2026, 1, 5))

    assert len(gaps) == 1
    assert gaps[0]["reason"] == "cancellation"
    assert gaps[0]["provider_id"] == str(seed["provider"].id)


def test_refilled_cancellation_is_not_a_gap(owner, seed_scheduling):
    seed = seed_scheduling()
    start = utc(2026, 1, 5, 10, 0)
    make_appointment(seed, start, status="cancelled")
    make_appointment(seed, start, status="scheduled")

    assert detect_gaps(seed["clinic"], date(2026, 1, 5), date(2026, 1, 5)) == []


def test_waitlist_entry_create_and_patch(owner, seed_scheduling):
    seed = seed_scheduling()
    entry = _create_entry(owner, seed, urgency=2)
    assert entry["status"] == "active"

    patched = owner.patch(f"{WAITLIST}{entry['id']}/", {"urgency": 5})
    assert patched.status_code == 200
    assert patched.json()["urgency"] == 5


def test_offer_blocks_the_slot_and_claim_is_atomic(owner, seed_scheduling):
    seed = seed_scheduling()
    first = _create_entry(owner, seed)
    second = _create_entry(owner, seed)
    start = utc(2026, 1, 6, 10, 0)

    created = _create_offer(owner, first["id"], seed, start)
    assert created.status_code == 201, created.json()
    token = created.json()["token"]
    offer_id = created.json()["id"]

    # The slot is held: a second offer on the same slot is rejected.
    conflict = _create_offer(owner, second["id"], seed, start)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "slot_conflict"

    claimed = owner.post(f"/api/v1/offers/{token}/claim/")
    assert claimed.status_code == 200, claimed.json()
    assert claimed.json()["status"] == "claimed"
    assert claimed.json()["claimed_appointment"] is not None

    offer = WaitlistOffer.all_objects.get(pk=offer_id)
    assert offer.status == WaitlistOffer.Status.CLAIMED
    appointment = Appointment.all_objects.get(pk=offer.claimed_appointment_id)
    assert appointment.status == "scheduled"
    assert WaitlistEntry.all_objects.get(pk=first["id"]).status == "claimed"


def test_second_claim_is_gone(owner, seed_scheduling):
    seed = seed_scheduling()
    entry = _create_entry(owner, seed)
    created = _create_offer(owner, entry["id"], seed, utc(2026, 1, 6, 11, 0))
    token = created.json()["token"]

    assert owner.post(f"/api/v1/offers/{token}/claim/").status_code == 200
    again = owner.post(f"/api/v1/offers/{token}/claim/")
    assert again.status_code == 410
    assert again.json()["error"]["code"] == "offer_unavailable"


def test_decline_releases_the_hold(owner, seed_scheduling):
    seed = seed_scheduling()
    entry = _create_entry(owner, seed)
    created = _create_offer(owner, entry["id"], seed, utc(2026, 1, 6, 12, 0))
    offer = WaitlistOffer.all_objects.get(pk=created.json()["id"])
    held_id = offer.held_appointment_id

    declined = owner.post(f"/api/v1/offers/{created.json()['token']}/decline/")

    assert declined.status_code == 200
    assert declined.json()["status"] == "declined"
    offer.refresh_from_db()
    assert offer.held_appointment_id is None
    assert Appointment.all_objects.get(pk=held_id).status == "cancelled"
    assert WaitlistEntry.all_objects.get(pk=entry["id"]).status == "active"


def test_expired_offer_releases_the_hold(owner, seed_scheduling):
    seed = seed_scheduling()
    entry = _create_entry(owner, seed)
    created = _create_offer(owner, entry["id"], seed, utc(2026, 1, 6, 13, 0))
    offer = WaitlistOffer.all_objects.get(pk=created.json()["id"])
    held_id = offer.held_appointment_id

    offer.expires_at = utc(2026, 1, 6, 12, 30)
    offer.save(update_fields=["expires_at", "updated_at"])

    result = offer_service.expire_offers(now=utc(2026, 1, 7, 9, 0))

    assert result["expired"] == 1
    offer.refresh_from_db()
    assert offer.status == WaitlistOffer.Status.EXPIRED
    assert offer.held_appointment_id is None
    assert Appointment.all_objects.get(pk=held_id).status == "cancelled"
    assert WaitlistEntry.all_objects.get(pk=entry["id"]).status == "active"


def test_waitlist_requires_manage_permission(assistant):
    response = assistant.get(WAITLIST)
    assert response.status_code == 403
