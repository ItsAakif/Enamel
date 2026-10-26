"""Public booking throttles: 10/min/IP and 3/min/phone."""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

BOOKINGS_URL = reverse("booking:public-booking-create")


def test_eleventh_request_from_one_ip_is_429(api_client):
    # No phone in the body, so only the per-IP scope applies. The requests do
    # not have to succeed: the throttle is evaluated before the view body.
    responses = [api_client.post(BOOKINGS_URL, {}, format="json") for _ in range(10)]
    assert all(response.status_code != 429 for response in responses)

    throttled = api_client.post(BOOKINGS_URL, {}, format="json")

    assert throttled.status_code == 429
    body = throttled.json()
    assert body["error"]["code"] == "throttled"
    assert int(throttled["Retry-After"]) > 0


def test_fourth_request_with_the_same_phone_is_429(api_client):
    payload = {"patient": {"phone": "+919876500009"}}

    for _ in range(3):
        response = api_client.post(BOOKINGS_URL, payload, format="json")
        assert response.status_code != 429

    throttled = api_client.post(BOOKINGS_URL, payload, format="json")

    assert throttled.status_code == 429
    assert throttled.json()["error"]["code"] == "throttled"
    assert int(throttled["Retry-After"]) > 0


def test_distinct_phones_do_not_trip_the_phone_scope(api_client):
    for index in range(3):
        response = api_client.post(
            BOOKINGS_URL,
            {"patient": {"phone": f"+919876501{index:03d}"}},
            format="json",
        )
        assert response.status_code != 429
