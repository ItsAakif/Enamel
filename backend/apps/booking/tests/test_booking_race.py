"""Concurrent public booking of one slot: exactly one winner."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connections
from django.urls import reverse
from rest_framework.test import APIClient

from apps.booking.models import BookingReference
from apps.patients.models import Patient
from apps.scheduling.models import Appointment

from .conftest import booking_payload, utc_slot

pytestmark = pytest.mark.django_db(transaction=True)

BOOKINGS_URL = reverse("booking:public-booking-create")


def _body(seed, slot, *, phone: str, key: str) -> dict:
    return booking_payload(
        seed,
        slot,
        idempotency_key=key,
        patient={
            "first_name": "Race",
            "last_name": phone[-4:],
            "date_of_birth": "1990-05-04",
            "phone": phone,
        },
    )


def test_concurrent_booking_of_one_slot_has_one_winner(seed):
    slot = utc_slot()
    bodies = [
        _body(seed, slot, phone="+919876510001", key="race-key-1"),
        _body(seed, slot, phone="+919876510002", key="race-key-2"),
    ]

    def send(body: dict) -> tuple[int, str]:
        connections.close_all()
        try:
            client = APIClient()
            response = client.post(BOOKINGS_URL, body, format="json")
            return response.status_code, response.json().get("error", {}).get("code", "")
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send, body) for body in bodies]
        results = [future.result(timeout=30) for future in futures]

    statuses = sorted(status for status, _ in results)
    assert statuses == [201, 409]
    conflict_codes = {code for status, code in results if status == 409}
    assert conflict_codes <= {"slot_conflict", "hold_expired"}

    clinic = seed["clinic"]
    assert BookingReference.all_objects.filter(clinic=clinic).count() == 1
    assert Appointment.all_objects.filter(clinic=clinic).count() == 1
    assert Patient.all_objects.filter(clinic=clinic).count() == 1
