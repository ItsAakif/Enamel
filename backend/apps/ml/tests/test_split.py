"""Leakage-safe split assertions (T131)."""

from __future__ import annotations

import pytest

from ml.no_show.dataset import build_synthetic_dataset, patient_overlap
from ml.no_show.train import (
    SplitLeakageError,
    assert_disjoint,
    group_split,
    temporal_split,
    three_way_split,
)


@pytest.fixture(scope="module")
def frame():
    return build_synthetic_dataset(42, 1200)


def test_group_split_has_zero_patient_overlap(frame):
    train, test = group_split(frame, seed=42)

    assert patient_overlap(train, test) == set()
    assert len(train) + len(test) == len(frame)
    assert len(train) > 0 and len(test) > 0


def test_three_way_split_is_patient_disjoint_everywhere(frame):
    fit, validation, holdout = three_way_split(frame, seed=42)

    assert patient_overlap(fit, validation) == set()
    assert patient_overlap(fit, holdout) == set()
    assert patient_overlap(validation, holdout) == set()
    assert len(fit) + len(validation) + len(holdout) == len(frame)


def test_split_guard_raises_when_a_patient_leaks(frame):
    left = frame.iloc[:200]
    right = frame.iloc[:50]  # deliberately shares patients with ``left``

    with pytest.raises(SplitLeakageError):
        assert_disjoint(left, right, left_name="train", right_name="test")


def test_group_split_is_reproducible_for_the_seed(frame):
    first_train, _ = group_split(frame, seed=42)
    second_train, _ = group_split(frame, seed=42)

    assert sorted(first_train["appointment_id"]) == sorted(second_train["appointment_id"])


def test_temporal_holdout_is_strictly_newer(frame):
    train, test = temporal_split(frame)

    assert len(train) > 0 and len(test) > 0
    assert train["start_at"].max() <= test["start_at"].min()
    # A patient may legitimately appear in both periods; only the grouped split
    # asserts zero overlap.
    assert patient_overlap(train, test)
