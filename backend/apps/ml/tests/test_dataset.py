"""Synthetic dataset: size, rate, determinism and the leakage guard (T131)."""

from __future__ import annotations

import pytest

from ml.no_show.config import FEATURE_NAMES, TARGET
from ml.no_show.dataset import (
    build_synthetic_dataset,
    dataset_hash,
    dataset_summary,
    no_show_rate,
)
from ml.no_show.features import FEATURES, validate_registry


def test_dataset_has_at_least_one_thousand_rows_and_a_documented_rate():
    frame = build_synthetic_dataset(42, 1200)
    summary = dataset_summary(frame)

    assert summary["n"] >= 1000
    assert summary["n"] == len(frame)
    assert summary["patients"] > 0
    # The rate is measured, not assumed, and is plausible (5%-40%).
    assert 0.05 <= summary["no_show_rate"] <= 0.40
    assert summary["no_show_rate"] == no_show_rate(frame)
    assert "Synthetic" in summary["disclosure"]


def test_dataset_is_deterministic_for_a_seed_and_changes_with_it():
    first = build_synthetic_dataset(42, 1000)
    second = build_synthetic_dataset(42, 1000)
    other = build_synthetic_dataset(7, 1000)

    assert dataset_hash(first) == dataset_hash(second)
    assert dataset_hash(first) != dataset_hash(other)


def test_every_feature_is_documented_with_a_reason():
    validate_registry()
    assert len(FEATURES) == len(FEATURE_NAMES)
    for feature in FEATURES:
        assert feature.reason.strip(), f"{feature.name} has no documented reason"
        assert feature.kind in {"numeric", "categorical", "boolean"}
        assert feature.description.strip()


def test_history_counters_only_see_strictly_earlier_appointments():
    """The future-data guard for the two riskiest features."""

    frame = build_synthetic_dataset(42, 1200)

    for patient_id, group in frame.groupby("patient_id"):
        ordered = group.sort_values("start_at", kind="mergesort")
        expected_no_show = 0
        expected_cancelled = 0
        expected_visits = 0
        for row in ordered.itertuples():
            total = expected_no_show + expected_cancelled + expected_visits
            assert row.prior_no_show_count == expected_no_show, patient_id
            assert row.prior_cancellations == expected_cancelled, patient_id
            assert row.prior_visits == expected_visits, patient_id
            assert row.prior_no_show_rate == round(
                expected_no_show / max(total, 1), 6
            ), patient_id
            assert row.is_new_patient == (total == 0), patient_id
            # Update *after* the assertion: the row may never see itself.
            if row.no_show:
                expected_no_show += 1
            elif row.cancelled:
                expected_cancelled += 1
            else:
                expected_visits += 1


def test_no_feature_column_is_a_copy_of_the_target():
    frame = build_synthetic_dataset(42, 1000)
    for name in FEATURE_NAMES:
        if frame[name].dtype == bool:
            assert not frame[name].equals(frame[TARGET].astype(bool))
        else:
            assert not frame[name].astype(str).equals(frame[TARGET].astype(str))


def test_cold_start_patients_have_zero_priors_not_missing_values():
    frame = build_synthetic_dataset(42, 1200)
    cold = frame[frame["is_new_patient"]]
    assert len(cold) > 0
    assert (cold["prior_no_show_count"] == 0).all()
    assert (cold["prior_cancellations"] == 0).all()
    assert (cold["prior_visits"] == 0).all()
    assert frame[list(FEATURE_NAMES)].isna().sum().sum() == 0


def test_dataset_rejects_a_too_small_request():
    with pytest.raises(ValueError):
        build_synthetic_dataset(42, 999)
