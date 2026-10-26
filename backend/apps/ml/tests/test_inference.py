"""Inference API: shape, missing features, scoping, retirement (T131)."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import joblib
import pytest

from apps.ml.models import NoShowModel, Prediction

from .conftest import utc

pytestmark = pytest.mark.django_db

MODELS_URL = "/api/v1/ml/no-show/models/"
PREDICT_URL = "/api/v1/ml/no-show/predict/"
PREDICTIONS_URL = "/api/v1/ml/no-show/predictions/"

COMPLETE_FEATURES = {"insurance_self_pay": "insurance", "distance_band": "2-5km"}


def _appointment(seed, make_appointment, **extra):
    return make_appointment(seed, utc(2026, 6, 1, 10, 0), **extra)


def test_model_list_returns_the_active_model_with_the_disclosure(
    owner, noshow_model
):
    response = owner.get(MODELS_URL)

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["count"] == 1
    row = body["results"][0]
    assert row["version"] == noshow_model.version
    assert row["status"] == "active"
    assert row["synthetic_data"] is True
    assert row["metrics_summary"]["candidate_roc_auc"] is not None
    assert "not" in row["metrics_summary"]["disclosure"].lower()
    assert "artifact_path" not in row


def test_predict_returns_a_probability_decision_and_factors(
    owner, noshow_model, seed_ml, make_appointment
):
    seed = seed_ml()
    appointment = _appointment(seed, make_appointment)

    response = owner.post(
        PREDICT_URL, {"appointment_id": str(appointment.id), "features": COMPLETE_FEATURES}
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert 0.0 <= body["probability"] <= 1.0
    assert body["threshold"] == noshow_model.threshold
    assert body["decision"] in {"high_risk", "low_risk"}
    assert body["model_version"] == noshow_model.version
    assert body["synthetic_data"] is True
    assert body["factors_method"] == "global_permutation_importance"
    assert isinstance(body["factors"], list) and body["factors"]
    for factor in body["factors"]:
        assert set(factor) == {"feature", "value", "importance"}
    # The decision is consistent with the threshold.
    expected = "high_risk" if body["probability"] >= body["threshold"] else "low_risk"
    assert body["decision"] == expected

    stored = Prediction.all_objects.get(pk=body["prediction_id"])
    assert stored.model_version == noshow_model.version
    assert stored.probability == body["probability"]


def test_missing_features_return_422_with_the_full_list(
    owner, noshow_model, seed_ml, make_appointment
):
    seed = seed_ml()
    appointment = _appointment(seed, make_appointment)

    response = owner.post(PREDICT_URL, {"appointment_id": str(appointment.id)})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert set(error["details"]["missing_features"]) == {
        "insurance_self_pay",
        "distance_band",
    }
    assert set(error["field_errors"]) == {"insurance_self_pay", "distance_band"}
    # Nothing is written when the vector is incomplete.
    assert Prediction.all_objects.count() == 0


def test_partial_overrides_still_report_the_remaining_gap(
    owner, noshow_model, seed_ml, make_appointment
):
    seed = seed_ml()
    appointment = _appointment(seed, make_appointment)

    response = owner.post(
        PREDICT_URL,
        {
            "appointment_id": str(appointment.id),
            "features": {"insurance_self_pay": "self_pay"},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["missing_features"] == ["distance_band"]


def test_patient_without_a_date_of_birth_has_a_missing_age_band(
    owner, noshow_model, seed_ml, make_appointment, patient_factory
):
    seed = seed_ml()
    patient = patient_factory(seed["clinic"], date_of_birth=None)
    appointment = _appointment(seed, make_appointment, patient=patient)

    response = owner.post(
        PREDICT_URL, {"appointment_id": str(appointment.id), "features": COMPLETE_FEATURES}
    )

    assert response.status_code == 422
    assert "patient_age_band" in response.json()["error"]["details"]["missing_features"]


def test_cold_start_patient_predicts_without_a_crash(
    owner, noshow_model, seed_ml, make_appointment
):
    seed = seed_ml()
    appointment = _appointment(seed, make_appointment)

    response = owner.post(
        PREDICT_URL, {"appointment_id": str(appointment.id), "features": COMPLETE_FEATURES}
    )

    assert response.status_code == 200
    assert response.json()["probability"] >= 0.0


def test_foreign_appointment_is_404(
    owner, noshow_model, staff_factory, make_appointment, location_factory
):
    from apps.clinics.models import Provider
    from apps.patients.models import Patient
    from apps.patients.services.numbering import allocate_patient_number
    from apps.scheduling.models import AppointmentType

    other = staff_factory(role="owner", slug="other-ml-clinic")
    foreign_seed = {
        "clinic": other.clinic,
        "location": location_factory(other.clinic),
        "provider": Provider.all_objects.create(
            clinic=other.clinic, membership=other.membership, display_name="Dr Other"
        ),
        "appointment_type": AppointmentType.all_objects.create(
            clinic=other.clinic, code="checkup", name="Checkup", duration_min=30
        ),
        "patient": Patient.all_objects.create(
            clinic=other.clinic,
            number=allocate_patient_number(other.clinic),
            first_name="Foreign",
            last_name="Patient",
            date_of_birth=datetime(1990, 1, 1).date(),
        ),
    }
    foreign = make_appointment(foreign_seed, utc(2026, 6, 1, 10, 0))

    response = owner.post(
        PREDICT_URL, {"appointment_id": str(foreign.id), "features": COMPLETE_FEATURES}
    )

    assert response.status_code == 404


def test_retired_model_is_unreachable_but_predictions_stay_readable(
    owner, noshow_model, seed_ml, make_appointment
):
    seed = seed_ml()
    appointment = _appointment(seed, make_appointment)
    first = owner.post(
        PREDICT_URL, {"appointment_id": str(appointment.id), "features": COMPLETE_FEATURES}
    )
    assert first.status_code == 200
    prediction_id = first.json()["prediction_id"]

    noshow_model.status = NoShowModel.Status.RETIRED
    noshow_model.save(update_fields=["status", "updated_at"])

    blocked = owner.post(
        PREDICT_URL, {"appointment_id": str(appointment.id), "features": COMPLETE_FEATURES}
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "conflict"

    listing = owner.get(PREDICTIONS_URL)
    assert listing.status_code == 200
    versions = [row["model_version"] for row in listing.json()["results"]]
    assert noshow_model.version in versions
    stored = Prediction.all_objects.get(pk=prediction_id)
    assert stored.model_version == noshow_model.version


def test_artifact_version_mismatch_fails_loudly(
    owner, trained_bundle, seed_ml, make_appointment, tmp_path
):
    mismatched = tmp_path / "mismatch.joblib"
    joblib.dump({"model": None, "sklearn_version": "0.0.0"}, mismatched)
    NoShowModel.objects.create(
        clinic=None,
        version="noshow-bad-version",
        algorithm="test",
        features=[],
        threshold=0.5,
        artifact_path=str(mismatched),
        status=NoShowModel.Status.ACTIVE,
        is_synthetic=True,
        sklearn_version="0.0.0",
    )

    seed = seed_ml()
    appointment = _appointment(seed, make_appointment)
    response = owner.post(
        PREDICT_URL, {"appointment_id": str(appointment.id), "features": COMPLETE_FEATURES}
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"


def test_artifact_path_from_another_host_falls_back_to_the_artifact_dir(
    owner, trained_bundle, noshow_model, seed_ml, make_appointment, tmp_path, settings
):
    """A model trained on a workstation is servable from a container.

    The registry row keeps the training host's absolute path. The same file is
    shipped under ``ML_ARTIFACT_DIR``; the loader must find it there instead of
    returning 503.
    """

    filename = Path(trained_bundle["artifact_path"]).name
    artifact_dir = tmp_path / "ml-artifacts"
    artifact_dir.mkdir()
    shutil.copyfile(trained_bundle["artifact_path"], artifact_dir / filename)
    settings.ML_ARTIFACT_DIR = str(artifact_dir)
    NoShowModel.objects.filter(pk=noshow_model.pk).update(
        artifact_path=rf"E:\Build\Enamel\backend\artifacts\ml\{filename}"
    )
    noshow_model.refresh_from_db()

    seed = seed_ml()
    appointment = _appointment(seed, make_appointment)
    response = owner.post(
        PREDICT_URL, {"appointment_id": str(appointment.id), "features": COMPLETE_FEATURES}
    )

    assert response.status_code == 200, response.json()
    assert response.json()["model_version"] == noshow_model.version


def test_unauthenticated_prediction_is_401(api_client, noshow_model, seed_ml, make_appointment):
    seed = seed_ml()
    appointment = _appointment(seed, make_appointment)

    response = api_client.post(
        PREDICT_URL, {"appointment_id": str(appointment.id)}, format="json"
    )

    assert response.status_code == 401
