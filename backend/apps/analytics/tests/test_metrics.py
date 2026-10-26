"""Analytics metrics against hand-computed values (T133).

Seed: June 2026 with a Monday-Friday 09:00-12:00 rule (180 minutes/day) and 22
weekdays, so the utilisation denominator is 22 * 180 = 3960 minutes. Seven
in-window appointments (30 minutes each): 3 completed, 1 cancelled, 1 no_show,
1 scheduled, 1 confirmed. One invoice (500000 minor, paid 300000, balance
200000) and two succeeded payments summing 300000, plus one pending payment.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.analytics import registry

from .conftest import EXPECTED_AVAILABLE_MINUTES, MONTH_END, MONTH_START

pytestmark = pytest.mark.django_db

PARAMS = {"from": MONTH_START.isoformat(), "to": MONTH_END.isoformat()}


def metric(body, key, *, currency=None):
    for item in body["metrics"]:
        if item["key"] == key and (currency is None or item.get("currency") == currency):
            return item
    raise AssertionError(f"metric {key!r} (currency={currency}) not in payload")


def test_appointments_match_hand_computed_values(owner, seeded_month):
    response = owner.get("/api/v1/analytics/appointments/", PARAMS)

    assert response.status_code == 200, response.json()
    body = response.json()
    assert metric(body, "appointments.total")["value"] == 7
    assert metric(body, "appointments.completed")["value"] == 3
    assert metric(body, "appointments.cancelled")["value"] == 1
    assert metric(body, "appointments.no_show")["value"] == 1
    assert metric(body, "appointments.completion_rate")["value"] == round(3 / 7, 4)
    assert metric(body, "appointments.cancellation_rate")["value"] == round(1 / 7, 4)
    assert metric(body, "appointments.no_show_rate")["value"] == round(1 / 7, 4)

    assert metric(body, "appointments.booked_minutes")["value"] == 150
    assert metric(body, "appointments.available_minutes")["value"] == EXPECTED_AVAILABLE_MINUTES
    utilisation = metric(body, "appointments.utilisation")
    assert utilisation["value"] == round(150 / EXPECTED_AVAILABLE_MINUTES, 4)
    assert utilisation["numerator_value"] == 150
    assert utilisation["denominator_value"] == EXPECTED_AVAILABLE_MINUTES

    assert metric(body, "patients.new")["value"] == 2
    assert metric(body, "patients.returning")["value"] == 1
    assert body["patients"] == {"distinct": 3, "new": 2, "returning": 1}
    assert body["by_status"]["completed"] == 3
    assert body["by_status"]["no_show"] == 1


def test_revenue_matches_hand_computed_values(owner, seeded_month):
    response = owner.get("/api/v1/analytics/revenue/", PARAMS)

    assert response.status_code == 200, response.json()
    body = response.json()
    assert metric(body, "revenue.invoiced_minor", currency="INR")["value"] == 500000
    assert metric(body, "revenue.collected_minor", currency="INR")["value"] == 300000
    assert metric(body, "revenue.outstanding_minor", currency="INR")["value"] == 200000
    rate = metric(body, "revenue.collection_rate", currency="INR")
    assert rate["value"] == round(300000 / 500000, 4)
    assert rate["denominator_value"] == 500000

    assert body["by_currency"]["collected_minor"] == {"INR": 300000}
    assert body["by_currency"]["invoiced_minor"] == {"INR": 500000}
    assert body["by_currency"]["outstanding_minor"] == {"INR": 200000}
    # The pending 999999 payment is not money.
    assert body["by_currency"]["collected_minor"] != {"INR": 1299999}


def test_recall_and_reminders_match_hand_computed_values(owner, seeded_month):
    response = owner.get("/api/v1/analytics/recall/", PARAMS)

    assert response.status_code == 200, response.json()
    body = response.json()
    assert metric(body, "recall.due")["value"] == 2
    assert metric(body, "recall.completed")["value"] == 1
    assert metric(body, "recall.completion_rate")["value"] == 0.5
    assert metric(body, "reminders.sent")["value"] == 2
    assert metric(body, "reminders.delivered")["value"] == 1
    assert metric(body, "reminders.failed")["value"] == 1
    assert metric(body, "reminders.delivery_rate")["value"] == 0.5


def test_waitlist_matches_hand_computed_values(owner, seeded_month):
    response = owner.get("/api/v1/analytics/waitlist/", PARAMS)

    assert response.status_code == 200, response.json()
    body = response.json()
    assert metric(body, "waitlist.offers")["value"] == 3
    assert metric(body, "waitlist.claims")["value"] == 1
    assert metric(body, "waitlist.expired")["value"] == 1
    rate = metric(body, "waitlist.claim_rate")
    assert rate["value"] == round(1 / 3, 4)
    assert rate["denominator_value"] == 3


def test_overview_exposes_the_headline_metrics_and_series(owner, seeded_month):
    response = owner.get("/api/v1/analytics/overview/", PARAMS)

    assert response.status_code == 200, response.json()
    body = response.json()
    keys = {item["key"] for item in body["metrics"]}
    assert {
        "appointments.total",
        "appointments.utilisation",
        "revenue.collected_minor",
        "recall.due",
        "waitlist.offers",
    } <= keys
    # The chart series is derived from persisted data, never a constant.
    assert len(body["appointments"]["series"]) == 30
    assert sum(point["total"] for point in body["appointments"]["series"]) == 7
    assert body["revenue"]["by_currency"]["collected_minor"] == {"INR": 300000}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/analytics/overview/",
        "/api/v1/analytics/appointments/",
        "/api/v1/analytics/revenue/",
        "/api/v1/analytics/recall/",
        "/api/v1/analytics/waitlist/",
    ],
)
def test_every_metric_payload_carries_its_definition_and_denominator(
    owner, seeded_month, path
):
    response = owner.get(path, PARAMS)

    assert response.status_code == 200, response.json()
    metrics = response.json()["metrics"]
    assert metrics
    for item in metrics:
        assert set(item) >= {
            "key",
            "label",
            "unit",
            "definition",
            "numerator",
            "denominator",
            "value",
            "numerator_value",
            "denominator_value",
        }
        assert item["definition"].strip()
        assert item["numerator"].strip()
        assert item["denominator"].strip()
        assert item["value"] is not None
        assert item["denominator_value"] is not None


def test_every_spec_has_a_registered_compute_function():
    assert set(registry.REGISTRY) == set(registry.metric_keys())
    assert len(registry.REGISTRY) >= 25


def test_empty_clinic_is_a_zero_state_not_nulls(owner):
    response = owner.get(
        "/api/v1/analytics/overview/",
        {"from": "2030-01-01", "to": "2030-01-31"},
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    for item in body["metrics"]:
        assert item["value"] == 0 or item["value"] == 0.0
        assert item["denominator_value"] == 0 or item["denominator_value"] == 1
        if item["unit"] == "ratio":
            assert item["denominator_value"] == 0
            assert item["value"] == 0.0
    assert body["appointments"]["series"]
    assert all(point["total"] == 0 for point in body["appointments"]["series"])


def test_range_block_documents_the_half_open_contract(owner, seeded_month):
    body = owner.get("/api/v1/analytics/appointments/", PARAMS).json()
    window = body["range"]

    assert window["from"] == "2026-06-01"
    assert window["to"] == "2026-06-30"
    assert window["timezone"] == "Asia/Kolkata"
    assert window["inclusive_start"] is True
    assert window["exclusive_end"] is True


def test_reversed_range_is_400(owner, seeded_month):
    response = owner.get(
        "/api/v1/analytics/appointments/", {"from": "2026-06-30", "to": "2026-06-01"}
    )

    assert response.status_code == 400
    assert "from" in response.json()["error"]["field_errors"]


def test_missing_bounds_is_400(owner, seeded_month):
    response = owner.get("/api/v1/analytics/appointments/", {"from": "2026-06-01"})

    assert response.status_code == 400
    assert "to" in response.json()["error"]["field_errors"]


def test_foreign_location_filter_is_404(owner, seeded_month, staff_factory):
    other = staff_factory(role="owner", slug="other-analytics-clinic")

    response = owner.get(
        "/api/v1/analytics/appointments/", {**PARAMS, "location": str(other.clinic.id)}
    )

    assert response.status_code == 404


def test_location_filter_scopes_the_denominator(
    owner, seeded_month, location_factory, provider_factory, make_appointment
):
    from apps.analytics.services import appointments

    other_location = location_factory(seeded_month["clinic"], name="Second")
    other_provider = provider_factory(seeded_month["clinic"], display_name="Dr Second")
    make_appointment(
        seeded_month["seed"],
        datetime(2026, 6, 8, 3, 30, tzinfo=UTC),
        location=other_location,
        provider=other_provider,
        status="completed",
    )

    scoped = appointments.report(
        seeded_month["clinic"], MONTH_START, MONTH_END, other_location
    )
    total = next(item for item in scoped["metrics"] if item["key"] == "appointments.total")
    assert total["value"] == 1
    available = next(
        item for item in scoped["metrics"] if item["key"] == "appointments.available_minutes"
    )
    assert available["value"] == 0


def test_requires_analytics_view_permission(assistant, seeded_month):
    response = assistant.get("/api/v1/analytics/appointments/", PARAMS)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


def test_unauthenticated_is_401(api_client, seeded_month):
    response = api_client.get("/api/v1/analytics/appointments/", PARAMS)

    assert response.status_code == 401
