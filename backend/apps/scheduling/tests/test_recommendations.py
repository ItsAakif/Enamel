"""Explainable recommendations and the synthetic report (T116 acceptance 2, 5, 6, 7)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.scheduling.models import WaitlistEntry, WaitlistOffer
from apps.scheduling.services import metrics_eval
from apps.scheduling.services.recommend import COMPONENT_WEIGHTS, score_entry

from .conftest import KOLKATA, make_appointment, utc

pytestmark = pytest.mark.django_db

WAITLIST = "/api/v1/waitlist/"
RECOMMENDATIONS = "/api/v1/scheduling/recommendations/"


def _local_future(days: int = 3, hour: int = 10):
    base = timezone.now() + timedelta(days=days)
    return base.astimezone(KOLKATA).replace(hour=hour, minute=0, second=0, microsecond=0)


def _create_entry(owner, seed, *, urgency: int = 3) -> dict:
    now = timezone.now()
    response = owner.post(
        WAITLIST,
        {
            "patient": str(seed["patient"].id),
            "appointment_type": str(seed["appointment_type"].id),
            "window_start": now.isoformat(),
            "window_end": (now + timedelta(days=10)).isoformat(),
            "urgency": urgency,
        },
    )
    assert response.status_code == 201, response.json()
    return response.json()


def test_score_breakdown_sums_to_total(owner, seed_scheduling):
    seed = seed_scheduling()
    entry = WaitlistEntry.all_objects.get(pk=_create_entry(owner, seed, urgency=5)["id"])
    gap = {
        "start": utc(2026, 1, 6, 10, 0),
        "end": utc(2026, 1, 6, 10, 30),
        "provider_id": str(seed["provider"].id),
        "operatory_id": None,
        "appointment_type_id": str(seed["appointment_type"].id),
    }

    score = score_entry(entry, gap, now=timezone.now())

    assert score["total"] == round(
        sum(component["contribution"] for component in score["components"]), 4
    )
    assert set(COMPONENT_WEIGHTS) == {component["key"] for component in score["components"]}
    assert score["reasons"]


def test_offer_persists_the_score_breakdown(owner, seed_scheduling):
    seed = seed_scheduling()
    entry = _create_entry(owner, seed)

    created = owner.post(
        f"{WAITLIST}{entry['id']}/offers/",
        {"start_at": utc(2026, 1, 6, 10, 0).isoformat(), "provider": str(seed["provider"].id)},
    )
    assert created.status_code == 201, created.json()
    payload = created.json()

    score = payload["score_json"]
    assert score["total"] == round(
        sum(component["contribution"] for component in score["components"]), 4
    )
    assert score["reasons"]

    offer = WaitlistOffer.all_objects.get(pk=payload["id"])
    assert offer.score_json["total"] == score["total"]


def test_recommendations_return_top_three_with_reasons(owner, seed_scheduling):
    seed = seed_scheduling()
    start = _local_future(days=3, hour=10)
    make_appointment(seed, start, status="cancelled")
    on_date = start.astimezone(KOLKATA).date()

    for urgency in (5, 4, 3, 2):
        _create_entry(owner, seed, urgency=urgency)

    response = owner.get(
        RECOMMENDATIONS,
        {"date": on_date.isoformat(), "type": str(seed["appointment_type"].id)},
    )

    assert response.status_code == 200, response.json()
    recommendations = response.json()["recommendations"]
    assert len(recommendations) == 3
    assert all(item["reasons"] for item in recommendations)
    assert recommendations[0]["total"] >= recommendations[-1]["total"]


def test_recommendations_respect_lead_time(owner, seed_scheduling):
    seed = seed_scheduling()
    past = timezone.now() - timedelta(days=1)
    make_appointment(seed, past, status="cancelled")
    on_date = past.astimezone(KOLKATA).date()
    _create_entry(owner, seed)

    response = owner.get(
        RECOMMENDATIONS,
        {"date": on_date.isoformat(), "type": str(seed["appointment_type"].id)},
    )

    assert response.json()["recommendations"] == []


def test_scenario_report_is_deterministic_and_beats_baseline():
    first = metrics_eval.run_cancel_fill(days=30, seed=42)
    second = metrics_eval.run_cancel_fill(days=30, seed=42)

    assert first == second
    assert first["waitlist"]["utilisation"] > first["baseline"]["utilisation"]
    assert first["waitlist"]["filled_from_waitlist"] > 0
    assert first["baseline"]["filled_from_waitlist"] == 0
    assert first["conflicts"] == 0
    assert "definitions" in first
    assert first["waitlist"]["offers"]["created"] >= first["waitlist"]["offers"]["claimed"]


def test_scenario_report_changes_with_the_seed():
    assert metrics_eval.run_cancel_fill(seed=1) != metrics_eval.run_cancel_fill(seed=2)
