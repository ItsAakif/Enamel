"""Optimistic concurrency for the odontogram (T108).

Writes lock the patient row with ``SELECT ... FOR UPDATE`` before touching any
tooth state, so two writers for the same patient serialise. The second one
re-reads the row *after* the first commits, sees the bumped version and gets a
409 with the current state. Because the check happens under the lock the test
is deterministic: exactly one request wins regardless of scheduling.
"""

from __future__ import annotations

import pytest
from django.db import connections

from apps.charting.models import ToothState, ToothStateEvent

pytestmark = pytest.mark.django_db


def test_sequential_stale_version_is_a_conflict(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    created = save_batch(
        owner,
        patient,
        [{"tooth": 16, "condition_code": "caries", "surfaces": ["O"]}],
        key="stale-1",
    )
    assert created.status_code == 200

    updated = save_batch(
        owner,
        patient,
        [{"tooth": 16, "condition_code": "caries", "version": 1, "surfaces": ["O", "M"]}],
        key="stale-2",
    )
    assert updated.status_code == 200
    assert updated.json()["applied"][0]["version"] == 2

    stale = save_batch(
        owner,
        patient,
        [{"tooth": 16, "condition_code": "caries", "version": 1, "surfaces": ["D"]}],
        key="stale-3",
    )
    assert stale.status_code == 409
    error = stale.json()["error"]
    assert error["code"] == "version_conflict"
    assert "changes.0.version" in error["field_errors"]
    assert error["details"]["current"]["version"] == 2
    assert error["details"]["current"]["surfaces"] == ["O", "M"]

    state = ToothState.all_objects.get(patient=patient, tooth=16)
    assert state.version == 2
    assert state.surfaces == ["O", "M"]


def test_version_increments_once_per_update(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    save_batch(
        owner,
        patient,
        [{"tooth": 21, "condition_code": "caries", "surfaces": ["M"]}],
        key="bump-1",
    )
    for expected in (2, 3, 4):
        response = save_batch(
            owner,
            patient,
            [
                {
                    "tooth": 21,
                    "condition_code": "caries",
                    "version": expected - 1,
                    "severity": expected,
                }
            ],
            key=f"bump-{expected}",
        )
        assert response.status_code == 200, response.json()
        assert response.json()["applied"][0]["version"] == expected
    assert ToothState.all_objects.get(patient=patient, tooth=21).version == 4


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_updates_yield_exactly_one_conflict(
    owner, patient_factory, save_batch
):
    from concurrent.futures import ThreadPoolExecutor

    from rest_framework.test import APIClient

    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    created = save_batch(
        owner,
        patient,
        [{"tooth": 36, "condition_code": "caries", "surfaces": ["M"]}],
        key="conc-create",
    )
    assert created.status_code == 200, created.json()
    state_id = created.json()["applied"][0]["id"]

    url = f"/api/v1/patients/{patient.id}/chart/batch/"
    clinic_id = str(owner.clinic.id)

    clients = [APIClient(), APIClient()]
    for client in clients:
        client.force_login(owner.user)

    def send(client, key: str, surfaces: list[str]):
        connections.close_all()
        try:
            response = client.post(
                url,
                {
                    "changes": [
                        {
                            "tooth": 36,
                            "condition_code": "caries",
                            "version": 1,
                            "surfaces": surfaces,
                        }
                    ],
                    "idempotency_key": key,
                },
                format="json",
                HTTP_X_CLINIC_ID=clinic_id,
            )
            return response.status_code, response.json()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(send, clients[0], "conc-a", ["M", "D"])
        second = pool.submit(send, clients[1], "conc-b", ["O"])
        results = [first.result(timeout=30), second.result(timeout=30)]

    statuses = sorted(status for status, _ in results)
    assert statuses == [200, 409], results

    conflict = next(body for status, body in results if status == 409)
    assert conflict["error"]["code"] == "version_conflict"
    current = conflict["error"]["details"]["current"]
    assert current["version"] == 2
    assert current["id"] == state_id

    winner = next(body for status, body in results if status == 200)
    assert winner["applied"][0]["version"] == 2

    state = ToothState.all_objects.get(pk=state_id)
    assert state.version == 2
    assert state.surfaces == current["surfaces"]
    assert state.surfaces in (["M", "D"], ["O"])

    # The loser wrote nothing: one create + one update event for this state.
    events = ToothStateEvent.all_objects.filter(tooth_state_id=state_id)
    assert events.count() == 2
    assert sorted(events.values_list("event_type", flat=True)) == ["created", "updated"]


@pytest.mark.django_db(transaction=True)
def test_concurrent_updates_on_different_teeth_both_succeed(
    owner, patient_factory, save_batch
):
    from concurrent.futures import ThreadPoolExecutor

    from rest_framework.test import APIClient

    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    url = f"/api/v1/patients/{patient.id}/chart/batch/"
    clinic_id = str(owner.clinic.id)

    clients = [APIClient(), APIClient()]
    for client in clients:
        client.force_login(owner.user)

    def send(client, key: str, tooth: int):
        connections.close_all()
        try:
            response = client.post(
                url,
                {
                    "changes": [
                        {"tooth": tooth, "condition_code": "caries", "surfaces": ["M"]}
                    ],
                    "idempotency_key": key,
                },
                format="json",
                HTTP_X_CLINIC_ID=clinic_id,
            )
            return response.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(send, clients[0], "diff-a", 11)
        second = pool.submit(send, clients[1], "diff-b", 12)
        statuses = sorted([first.result(timeout=30), second.result(timeout=30)])

    assert statuses == [200, 200]
    assert ToothState.all_objects.filter(patient=patient).count() == 2
