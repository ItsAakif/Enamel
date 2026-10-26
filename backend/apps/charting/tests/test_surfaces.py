"""Surface validation and JSONB round-trip (T108)."""

from __future__ import annotations

import pytest

from apps.charting import fdi, validators
from apps.charting.models import ToothState

pytestmark = pytest.mark.django_db


# --- pure validator --------------------------------------------------------


def test_allowed_values_are_the_documented_six():
    assert set(validators.SURFACES) == {"M", "O", "D", "B", "L", "I"}


@pytest.mark.parametrize("code", [11, 12, 13, 21, 31, 41])
def test_anterior_teeth_accept_the_anterior_set(code):
    info = fdi.tooth_info(code)
    assert validators.validate_surfaces(["M", "D", "B", "L", "I"], info) == [
        "M",
        "D",
        "B",
        "L",
        "I",
    ]


@pytest.mark.parametrize("code", [14, 15, 16, 17, 18, 46, 54, 55])
def test_posterior_teeth_accept_the_posterior_set(code):
    info = fdi.tooth_info(code)
    assert validators.validate_surfaces(["M", "O", "D", "B", "L"], info) == [
        "M",
        "O",
        "D",
        "B",
        "L",
    ]


def test_incisal_surface_is_rejected_on_posterior_teeth():
    with pytest.raises(validators.ChartValidationError) as excinfo:
        validators.validate_surfaces(["I"], fdi.tooth_info(16))
    assert "surfaces" in excinfo.value.field_errors
    assert "not valid" in excinfo.value.field_errors["surfaces"][0]


def test_occlusal_surface_is_rejected_on_anterior_teeth():
    with pytest.raises(validators.ChartValidationError) as excinfo:
        validators.validate_surfaces(["O"], fdi.tooth_info(11))
    assert "surfaces" in excinfo.value.field_errors


def test_duplicate_surfaces_are_rejected():
    with pytest.raises(validators.ChartValidationError) as excinfo:
        validators.validate_surfaces(["M", "M"], fdi.tooth_info(16))
    assert any("duplicate" in message for message in excinfo.value.field_errors["surfaces"])


def test_unknown_surfaces_are_rejected():
    with pytest.raises(validators.ChartValidationError) as excinfo:
        validators.validate_surfaces(["X"], fdi.tooth_info(16))
    assert any("unknown surface" in message for message in excinfo.value.field_errors["surfaces"])


def test_non_list_surfaces_are_rejected():
    with pytest.raises(validators.ChartValidationError):
        validators.validate_surfaces("MOD", fdi.tooth_info(16))


def test_empty_surfaces_are_allowed():
    assert validators.validate_surfaces([], fdi.tooth_info(16)) == []
    assert validators.validate_surfaces(None, fdi.tooth_info(16)) == []


# --- API -------------------------------------------------------------------


def test_batch_rejects_incisal_on_posterior_with_a_field_error(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    response = save_batch(
        owner,
        patient,
        [{"tooth": 16, "condition_code": "caries", "surfaces": ["I"]}],
        key="surfaces-posterior-i",
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert "changes.0.surfaces" in error["field_errors"]
    assert ToothState.all_objects.filter(patient=patient).count() == 0


def test_batch_rejects_occlusal_on_anterior_with_a_field_error(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    response = save_batch(
        owner,
        patient,
        [{"tooth": 11, "condition_code": "caries", "surfaces": ["O"]}],
        key="surfaces-anterior-o",
    )
    assert response.status_code == 400
    assert "changes.0.surfaces" in response.json()["error"]["field_errors"]
    assert ToothState.all_objects.filter(patient=patient).count() == 0


def test_batch_accepts_a_valid_anterior_set(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    response = save_batch(
        owner,
        patient,
        [{"tooth": 11, "condition_code": "caries", "surfaces": ["M", "D", "I"]}],
        key="surfaces-anterior-ok",
    )
    assert response.status_code == 200, response.json()
    state = ToothState.all_objects.get(patient=patient, tooth=11, condition_id="caries")
    assert state.surfaces == ["M", "D", "I"]


def test_surfaces_round_trip_as_a_json_list(owner, patient_factory, save_batch, chart_urls):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    wanted = ["O", "M", "D"]
    response = save_batch(
        owner,
        patient,
        [{"tooth": 36, "condition_code": "restoration_composite", "surfaces": wanted}],
        key="surfaces-round-trip",
    )
    assert response.status_code == 200, response.json()

    state = ToothState.all_objects.get(patient=patient, tooth=36)
    assert isinstance(state.surfaces, list)
    assert state.surfaces == wanted
    assert set(state.surfaces) == set(wanted)

    reloaded = owner.get(chart_urls.chart(patient.id))
    tooth = next(entry for entry in reloaded.json()["teeth"] if entry["tooth"] == 36)
    assert tooth["states"][0]["surfaces"] == wanted
    assert isinstance(tooth["states"][0]["surfaces"], list)


def test_no_surface_edits_on_a_missing_tooth(
    owner, patient_factory, save_batch, chart_urls
):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    first = save_batch(
        owner,
        patient,
        [{"tooth": 46, "condition_code": "missing"}],
        key="missing-first",
    )
    assert first.status_code == 200, first.json()

    blocked = save_batch(
        owner,
        patient,
        [{"tooth": 46, "condition_code": "caries", "surfaces": ["O"]}],
        key="missing-surface-edit",
    )
    assert blocked.status_code == 400
    assert "changes.0.surfaces" in blocked.json()["error"]["field_errors"]
    assert ToothState.all_objects.filter(patient=patient).count() == 1

    # The missing condition itself may still carry its own (empty) surface set.
    allowed = save_batch(
        owner,
        patient,
        [{"tooth": 46, "condition_code": "missing", "version": 1, "note": "extracted"}],
        key="missing-own-edit",
    )
    assert allowed.status_code == 200, allowed.json()
