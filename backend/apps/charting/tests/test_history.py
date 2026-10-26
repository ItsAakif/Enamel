"""Append-only history: ordering, cursor pagination and replay (T108)."""

from __future__ import annotations

import pytest

from apps.charting.models import ChartHistoryImmutableError, ToothState, ToothStateEvent
from apps.charting.services.history import replay_patient

pytestmark = pytest.mark.django_db


def _create_state(owner, patient, save_batch, key, **change):
    response = save_batch(owner, patient, [change], key=key)
    assert response.status_code == 200, response.json()
    return response.json()["applied"][0]["id"]


def test_history_is_newest_first(owner, patient_factory, save_batch, chart_urls):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    state_id = _create_state(
        owner, patient, save_batch, "hist-1",
        tooth=11, condition_code="caries", surfaces=["M"],
    )
    save_batch(
        owner,
        patient,
        [{"tooth": 11, "condition_code": "caries", "version": 1, "surfaces": ["M", "D"]}],
        key="hist-2",
    )

    response = owner.get(chart_urls.history(patient.id))
    assert response.status_code == 200
    body = response.json()
    assert body["previous"] is None
    results = body["results"]
    assert [row["event_type"] for row in results] == ["updated", "created"]
    assert all(row["tooth"] == 11 for row in results)
    assert results[0]["version"] == 2
    assert results[0]["before"]["surfaces"] == ["M"]
    assert results[0]["after"]["surfaces"] == ["M", "D"]
    assert results[1]["after"]["id"] == state_id
    assert results[1]["before"] is None


def test_history_cursor_pagination_walks_without_overlap(
    owner, patient_factory, save_batch, chart_urls
):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    _create_state(
        owner, patient, save_batch, "page-0",
        tooth=11, condition_code="caries", surfaces=["M"],
    )
    for index in range(1, 6):
        save_batch(
            owner,
            patient,
            [{"tooth": 11, "condition_code": "caries", "version": index, "severity": index}],
            key=f"page-{index}",
        )

    seen: list[str] = []
    url: str | None = chart_urls.history(patient.id)
    params = {"page_size": 2}
    for _ in range(10):
        body = owner.get(url, params).json()
        seen.extend(row["id"] for row in body["results"])
        if not body["next"]:
            break
        url = body["next"]
        params = None

    assert len(seen) == 6
    assert len(set(seen)) == 6
    # Newest-first across the whole stream.
    newest = owner.get(chart_urls.history(patient.id), {"page_size": 100}).json()["results"]
    assert [row["version"] for row in newest] == [6, 5, 4, 3, 2, 1]


def test_history_can_filter_by_tooth(owner, patient_factory, save_batch, chart_urls):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    _create_state(owner, patient, save_batch, "filter-1", tooth=11, condition_code="caries")
    _create_state(owner, patient, save_batch, "filter-2", tooth=12, condition_code="caries")

    body = owner.get(chart_urls.history(patient.id), {"tooth": 11}).json()
    assert body["results"]
    assert {row["tooth"] for row in body["results"]} == {11}


def test_history_replays_to_the_current_state(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    _create_state(
        owner, patient, save_batch, "replay-1",
        tooth=11, condition_code="caries", surfaces=["M"],
    )
    save_batch(
        owner,
        patient,
        [{"tooth": 11, "condition_code": "caries", "version": 1, "surfaces": ["M", "D"]}],
        key="replay-2",
    )
    save_batch(
        owner,
        patient,
        [{"tooth": 11, "condition_code": "crown"}],
        key="replay-3",
    )
    save_batch(
        owner,
        patient,
        [{"tooth": 11, "condition_code": "crown", "archive": True}],
        key="replay-4",
    )
    state_id = ToothState.all_objects.get(patient=patient, tooth=11, condition_id="caries").id
    transition = owner.post(
        f"/api/v1/chart/states/{state_id}/transition/",
        {"to_status": "planned"},
    )
    assert transition.status_code == 200, transition.json()

    replayed = replay_patient(patient)
    current = {
        (row.tooth, row.condition_id): (row.status, list(row.surfaces), row.version)
        for row in ToothState.all_objects.filter(patient=patient, archived_at__isnull=True)
    }
    assert set(replayed) == set(current)
    for key, after in replayed.items():
        assert (after["status"], after["surfaces"], after["version"]) == current[key]
    # The archived crown layer is gone from both.
    assert (11, "crown") not in current


def test_history_rows_are_insert_only(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    _create_state(owner, patient, save_batch, "immutable-1", tooth=11, condition_code="caries")

    event = ToothStateEvent.all_objects.get(patient=patient)
    event.event_type = "updated"
    with pytest.raises(ChartHistoryImmutableError):
        event.save()
    with pytest.raises(ChartHistoryImmutableError):
        event.delete()

    # The stored row is untouched.
    stored = ToothStateEvent.all_objects.get(pk=event.pk)
    assert stored.event_type == "created"


def test_history_events_carry_the_actor_and_batch_id(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    _create_state(owner, patient, save_batch, "actor-1", tooth=11, condition_code="caries")

    event = ToothStateEvent.all_objects.get(patient=patient)
    assert event.actor_id == owner.user.id
    assert event.batch_id is not None
    assert event.idempotency_key == "actor-1"
    assert event.clinic_id == owner.clinic.id
    assert event.patient_id == patient.id
    assert event.tooth_state_id is not None


def test_history_for_a_foreign_patient_is_404(owner, staff_factory, patient_factory, chart_urls):
    other = staff_factory(role="owner")
    foreign = patient_factory(other.clinic)
    assert owner.get(chart_urls.history(foreign.id)).status_code == 404
