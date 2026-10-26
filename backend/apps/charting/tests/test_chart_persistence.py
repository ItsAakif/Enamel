"""Chart persistence, dentition resolution and tenant isolation (T108)."""

from __future__ import annotations

import pytest

from apps.charting.models import ConditionCode, ToothState
from apps.patients.models import Patient

pytestmark = pytest.mark.django_db


def _states_for(payload: dict, tooth: int) -> list[dict]:
    entry = next(item for item in payload["teeth"] if item["tooth"] == tooth)
    return entry["states"]


def test_create_then_read_round_trips_the_state(owner, patient_factory, save_batch, chart_urls):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    response = save_batch(
        owner,
        patient,
        [
            {
                "tooth": 26,
                "condition_code": "restoration_composite",
                "status": "existing",
                "surfaces": ["O", "D"],
                "severity": 2,
                "material": "composite",
                "note": "distal margin",
            }
        ],
        key="persist-1",
    )
    assert response.status_code == 200, response.json()
    assert response.json()["applied"][0]["action"] == "created"
    assert response.json()["applied"][0]["version"] == 1

    chart = owner.get(chart_urls.chart(patient.id))
    assert chart.status_code == 200
    payload = chart.json()

    assert payload["dentition"] == "permanent"
    assert payload["dentition_source"] == "derived"
    assert len(payload["teeth"]) == 32

    states = _states_for(payload, 26)
    assert len(states) == 1
    state = states[0]
    assert state["condition_code"] == "restoration_composite"
    assert state["status"] == "existing"
    assert state["surfaces"] == ["O", "D"]
    assert state["severity"] == 2
    assert state["material"] == "composite"
    assert state["note"] == "distal margin"
    assert state["version"] == 1
    assert state["plan_item_id"] is None

    info = next(item for item in payload["teeth"] if item["tooth"] == 26)["info"]
    assert info["arch"] == "upper"
    assert info["laterality"] == "left"
    assert info["type"] == "molar"


def test_counts_and_legend(owner, patient_factory, save_batch, chart_urls):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    save_batch(
        owner,
        patient,
        [
            {"tooth": 11, "condition_code": "caries", "surfaces": ["M"]},
            {"tooth": 12, "condition_code": "caries", "surfaces": ["D"]},
            {"tooth": 36, "condition_code": "crown"},
        ],
        key="counts-1",
    )
    payload = owner.get(chart_urls.chart(patient.id)).json()
    counts = payload["counts"]
    assert counts["total"] == 3
    assert counts["teeth_with_states"] == 3
    assert counts["by_status"] == {"existing": 3}
    assert counts["by_condition"] == {"caries": 2, "crown": 1}

    legend = {entry["code"]: entry for entry in payload["legend"]}
    assert "caries" in legend and "crown" in legend
    assert legend["caries"]["label"] == "Caries"
    assert legend["caries"]["surfaces_required"] is True
    assert legend["crown"]["surfaces_required"] is False
    # The full seeded catalogue is exposed for the UI legend.
    assert set(legend) == {row.code for row in ConditionCode.objects.all()}


def test_only_active_states_are_returned(owner, patient_factory, save_batch, chart_urls):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    save_batch(
        owner,
        patient,
        [{"tooth": 14, "condition_code": "caries", "surfaces": ["O"]}],
        key="active-1",
    )
    archived = save_batch(
        owner,
        patient,
        [{"tooth": 14, "condition_code": "caries", "archive": True}],
        key="active-2",
    )
    assert archived.status_code == 200, archived.json()
    assert archived.json()["applied"][0]["action"] == "archived"

    payload = owner.get(chart_urls.chart(patient.id)).json()
    assert _states_for(payload, 14) == []
    assert payload["counts"]["total"] == 0

    # The row is archived, never deleted.
    assert ToothState.all_objects.filter(patient=patient, tooth=14).count() == 1
    assert ToothState.all_objects.get(patient=patient, tooth=14).archived_at is not None


def test_re_adding_after_archive_starts_a_new_version_chain(
    owner, patient_factory, save_batch
):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    first = save_batch(
        owner,
        patient,
        [{"tooth": 24, "condition_code": "caries", "surfaces": ["O"]}],
        key="readd-1",
    )
    assert first.status_code == 200
    first_id = first.json()["applied"][0]["id"]

    assert (
        save_batch(
            owner,
            patient,
            [{"tooth": 24, "condition_code": "caries", "archive": True}],
            key="readd-2",
        ).status_code
        == 200
    )

    second = save_batch(
        owner,
        patient,
        [{"tooth": 24, "condition_code": "caries", "surfaces": ["M"]}],
        key="readd-3",
    )
    assert second.status_code == 200, second.json()
    applied = second.json()["applied"][0]
    assert applied["action"] == "created"
    assert applied["version"] == 1
    assert applied["id"] != first_id

    rows = list(ToothState.all_objects.filter(patient=patient, tooth=24).order_by("created_at"))
    assert len(rows) == 2
    assert rows[0].archived_at is not None
    assert rows[1].archived_at is None
    assert rows[1].version == 1


def test_dentition_is_derived_for_a_child_and_mixed_for_school_age(
    owner, patient_factory, chart_urls
):
    from datetime import date

    from django.utils import timezone

    def dob_years_ago(years: int) -> str:
        today = timezone.localdate()
        try:
            born = today.replace(year=today.year - years)
        except ValueError:  # 29 February
            born = today.replace(year=today.year - years, day=28)
        return born.isoformat()

    assert date.fromisoformat(dob_years_ago(2)) < timezone.localdate()

    child = patient_factory(owner.clinic, date_of_birth=dob_years_ago(2))
    payload = owner.get(chart_urls.chart(child.id)).json()
    assert payload["dentition"] == "primary"
    assert payload["dentition_source"] == "derived"
    assert len(payload["teeth"]) == 20

    school = patient_factory(owner.clinic, date_of_birth=dob_years_ago(8))
    mixed = owner.get(chart_urls.chart(school.id)).json()
    assert mixed["dentition"] == "mixed"
    assert len(mixed["teeth"]) == 52

    adult = patient_factory(owner.clinic, date_of_birth=dob_years_ago(30))
    assert owner.get(chart_urls.chart(adult.id)).json()["dentition"] == "permanent"


def test_dentition_override_is_read_only_and_reported(
    owner, patient_factory, chart_urls
):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    response = owner.get(chart_urls.chart(patient.id), {"dentition": "primary"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["dentition"] == "primary"
    assert payload["dentition_source"] == "override"
    assert len(payload["teeth"]) == 20


def test_invalid_dentition_override_is_a_field_error(owner, patient_factory, chart_urls):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    response = owner.get(chart_urls.chart(patient.id), {"dentition": "banana"})
    assert response.status_code == 400
    assert "dentition" in response.json()["error"]["field_errors"]


def test_explicit_dentition_mismatch_is_rejected(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    response = save_batch(
        owner,
        patient,
        [{"tooth": 51, "condition_code": "caries", "surfaces": ["M"]}],
        key="mismatch-1",
        dentition="permanent",
    )
    assert response.status_code == 400
    assert "changes.0.tooth" in response.json()["error"]["field_errors"]
    assert ToothState.all_objects.filter(patient=patient).count() == 0

    # The same change is accepted when the dentition context allows it.
    ok = save_batch(
        owner,
        patient,
        [{"tooth": 51, "condition_code": "caries", "surfaces": ["M"]}],
        key="mismatch-2",
        dentition="mixed",
    )
    assert ok.status_code == 200, ok.json()


def test_foreign_patient_is_404(owner, staff_factory, patient_factory, chart_urls):
    other = staff_factory(role="owner")
    foreign = patient_factory(other.clinic, date_of_birth="1990-01-01")

    assert owner.get(chart_urls.chart(foreign.id)).status_code == 404
    assert owner.get(chart_urls.history(foreign.id)).status_code == 404
    assert (
        owner.post(
            chart_urls.batch(foreign.id),
            {"changes": [{"tooth": 11, "condition_code": "caries"}], "idempotency_key": "x"},
        ).status_code
        == 404
    )


def test_patient_a_chart_never_appears_in_patient_b(
    owner, staff_factory, patient_factory, save_batch, chart_urls
):
    other = staff_factory(role="owner")
    patient_a = patient_factory(owner.clinic, first_name="Ann", date_of_birth="1990-01-01")
    patient_b = patient_factory(other.clinic, first_name="Ben", date_of_birth="1990-01-01")

    save_batch(
        owner,
        patient_a,
        [{"tooth": 17, "condition_code": "caries", "surfaces": ["O"], "note": "A only"}],
        key="iso-a",
    )
    save_batch(
        other,
        patient_b,
        [{"tooth": 27, "condition_code": "crown", "note": "B only"}],
        key="iso-b",
    )

    chart_a = owner.get(chart_urls.chart(patient_a.id)).json()
    chart_b = other.get(chart_urls.chart(patient_b.id)).json()

    a_teeth = {tooth for tooth in (17, 27) if _states_for(chart_a, tooth)}
    b_teeth = {tooth for tooth in (17, 27) if _states_for(chart_b, tooth)}
    assert a_teeth == {17}
    assert b_teeth == {27}

    a_ids = {state["id"] for state in _states_for(chart_a, 17)}
    b_ids = {state["id"] for state in _states_for(chart_b, 27)}
    assert a_ids.isdisjoint(b_ids)


def test_archived_patient_chart_is_read_only(owner, patient_factory, save_batch, chart_urls):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    save_batch(
        owner,
        patient,
        [{"tooth": 11, "condition_code": "caries", "surfaces": ["M"]}],
        key="archived-1",
    )

    Patient.all_objects.filter(pk=patient.pk).update(status="archived")
    patient.refresh_from_db()

    # Reads stay available for an archived (not deleted) record.
    assert owner.get(chart_urls.chart(patient.id)).status_code == 200

    response = save_batch(
        owner,
        patient,
        [{"tooth": 12, "condition_code": "caries", "surfaces": ["M"]}],
        key="archived-2",
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "patient_archived"
    assert ToothState.all_objects.filter(patient=patient, tooth=12).count() == 0


def test_soft_deleted_patient_is_hidden(owner, patient_factory, save_batch, chart_urls):
    from django.utils import timezone

    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    Patient.all_objects.filter(pk=patient.pk).update(deleted_at=timezone.now())

    assert owner.get(chart_urls.chart(patient.id)).status_code == 404
    response = save_batch(
        owner,
        patient,
        [{"tooth": 11, "condition_code": "caries"}],
        key="deleted-1",
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "patient_archived"


def test_patient_detail_chart_summary_lights_up(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    detail = owner.get(f"/api/v1/patients/{patient.id}/")
    assert detail.json()["chart_summary"] == {"available": True, "total": 0, "by_status": {}}

    save_batch(
        owner,
        patient,
        [{"tooth": 11, "condition_code": "caries", "surfaces": ["M"]}],
        key="summary-1",
    )
    detail = owner.get(f"/api/v1/patients/{patient.id}/")
    assert detail.json()["chart_summary"] == {
        "available": True,
        "total": 1,
        "by_status": {"existing": 1},
    }


def test_chart_responses_are_not_cached(owner, patient_factory, chart_urls):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    response = owner.get(chart_urls.chart(patient.id))
    assert response["Cache-Control"] == "no-store"


def test_chart_requires_authentication(api_client, patient_factory, clinic_factory, chart_urls):
    clinic = clinic_factory(slug="chart-anon")
    patient = patient_factory(clinic)
    response = api_client.get(
        chart_urls.chart(patient.id), HTTP_X_CLINIC_ID=str(clinic.id)
    )
    assert response.status_code == 401


def test_receptionist_can_read_but_not_write(staff_factory, patient_factory, save_batch, chart_urls):
    receptionist = staff_factory(role="receptionist")
    patient = patient_factory(receptionist.clinic, date_of_birth="1990-01-01")

    assert receptionist.get(chart_urls.chart(patient.id)).status_code == 200
    response = save_batch(
        receptionist,
        patient,
        [{"tooth": 11, "condition_code": "caries"}],
        key="recep-1",
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"
