"""Synthetic no-show dataset builder (T131).

There is no real appointment history in this environment, so the pipeline
generates a synthetic cohort. The generator is a *fixed* logistic process: a
seed alone determines every row, which is what makes the training script
reproducible and the metrics hash stable.

Honesty constraints
-------------------
* The data is **synthetic** and every consumer is told so
  (``config.SYNTHETIC_DISCLOSURE``; the inference API returns
  ``synthetic_data=true``). Nothing here is evidence of clinical validity.
* Features are computed as of the *booking instant* only. The patient history
  counters walk the patient's appointments in chronological order and look
  strictly backwards, so an appointment can never see its own outcome (the
  "future-data guard" test in ``tests/test_dataset.py`` pins this).
* The target is ``no_show``; a separate ``cancelled`` flag is generated only so
  the booking-time history counters have something to count. It is not the
  target and never a feature of the current row.
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd

from .config import (
    CATEGORICAL_FEATURES,
    DEFAULT_DATASET_SIZE,
    DEFAULT_SEED,
    FEATURE_NAMES,
    ID_COLUMNS,
    TARGET,
    TARGET_NO_SHOW_RATE,
)
from .features import lead_time_band

#: The synthetic generator is documented as a fixed logistic process. The
#: intercept is calibrated so the achieved no-show rate lands near
#: ``config.TARGET_NO_SHOW_RATE`` (the measured rate is reported, not assumed).
_INTERCEPT = -1.85
_PROVIDER_COUNT = 8
_AGE_BANDS = CATEGORICAL_FEATURES["patient_age_band"]
_DISTANCE_BANDS = CATEGORICAL_FEATURES["distance_band"]
_APPOINTMENT_TYPES = CATEGORICAL_FEATURES["appointment_type"]
_SOURCES = CATEGORICAL_FEATURES["source"]
_INSURANCE = CATEGORICAL_FEATURES["insurance_self_pay"]

#: Base timestamps keep every synthetic row inside a fixed, reproducible window.
_START = pd.Timestamp("2024-01-01T00:00:00+00:00")


def _sigmoid(value: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-value))


def _logit(
    *,
    lead_time_days: float,
    hour_of_day: int,
    age_index: int,
    distance_index: int,
    insurance: str,
    source: str,
    reminder_consent: bool,
    prior_no_show_rate: float,
    prior_no_show_count: int,
    prior_visits: int,
    reliability: float,
) -> float:
    """The documented synthetic data-generating process."""

    return (
        _INTERCEPT
        + 0.010 * min(lead_time_days, 30.0)
        + 0.02 * (hour_of_day - 12)
        + 0.05 * age_index
        + 0.08 * distance_index
        + (0.15 if insurance == "self_pay" else 0.0)
        + {"staff": 0.0, "online": 0.15, "ai": 0.10, "phone": 0.05}[source]
        + (-0.35 if reminder_consent else 0.0)
        + 0.2 * prior_no_show_rate
        + (0.05 if prior_visits == 0 else 0.0)
        + 1.0 * (reliability - 0.5)
        # Interaction-dominated structure. A long lead time is only risky when
        # it lands in a late-afternoon slot; an unreliable history is only risky
        # for patients who travel far. The other three cells of each pair are
        # deliberately *cheaper*, so the two main effects largely cancel and a
        # purely additive baseline cannot represent the signal. This is what a
        # tree model is for.
        + (
            2.0
            if (lead_time_days >= 10.0 and hour_of_day >= 14)
            else (-0.9 if (lead_time_days >= 10.0 or hour_of_day >= 14) else 0.35)
        )
        + (
            1.3
            if (prior_no_show_rate >= 0.25 and distance_index >= 2)
            else (-0.6 if (prior_no_show_rate >= 0.25 or distance_index >= 2) else 0.2)
        )
        # Step effect: a second prior no-show changes behaviour more than a
        # smooth count would.
        + (0.7 if prior_no_show_count >= 2 else 0.0)
    )


def build_synthetic_dataset(
    seed: int = DEFAULT_SEED,
    size: int = DEFAULT_DATASET_SIZE,
) -> pd.DataFrame:
    """Build the synthetic dataset.

    Returns a dataframe with ``ID_COLUMNS``, the 15 features, the target
    ``no_show``, the ``cancelled`` helper flag and the ``SUBGROUP_COLUMNS``.
    Rows are ordered by ``(patient_id, start_at)``.
    """

    if size < 1000:
        raise ValueError("the synthetic dataset must contain at least 1,000 rows")

    rng = np.random.RandomState(seed)
    rows: list[dict[str, Any]] = []
    patient_index = 0

    while len(rows) < size:
        patient_index += 1
        patient_id = f"pat-{patient_index:05d}"
        age_index = int(rng.randint(0, len(_AGE_BANDS)))
        distance_index = int(rng.randint(0, len(_DISTANCE_BANDS)))
        insurance = _INSURANCE[int(rng.randint(0, len(_INSURANCE)))]
        # Latent reliability drives both the history counters and the target,
        # which is exactly the patient-level correlation that makes a grouped
        # split necessary.
        reliability = float(rng.beta(2.5, 5.0))
        consent_base = float(rng.uniform(0.4, 0.95))

        appointment_count = int(rng.randint(3, 9))
        prior_no_show_count = 0
        prior_cancellations = 0
        prior_visits = 0
        # Appointments are generated in chronological order within a patient, so
        # "generated earlier" and "happened earlier" are the same thing and the
        # backwards-looking history counters are exactly booking-time history.
        current_day = float(rng.uniform(0.0, 60.0))

        for _ in range(appointment_count):
            lead_time_days = float(
                round(float(rng.exponential(scale=7.0)), 2)
            )
            hour_of_day = int(rng.choice([8, 9, 10, 11, 13, 14, 15, 16]))
            weekday = int(rng.randint(0, 5))
            appointment_type = _APPOINTMENT_TYPES[
                int(rng.randint(0, len(_APPOINTMENT_TYPES)))
            ]
            source = _SOURCES[int(rng.randint(0, len(_SOURCES)))]
            provider_tenure_days = float(
                round(float(rng.uniform(30.0, 2500.0)), 2)
            )
            reminder_consent = bool(rng.random() < consent_base)

            prior_no_show_rate = (
                prior_no_show_count
                / max(prior_no_show_count + prior_visits + prior_cancellations, 1)
            )
            probability = float(
                _sigmoid(
                    np.array(
                        [
                            _logit(
                                lead_time_days=lead_time_days,
                                hour_of_day=hour_of_day,
                                age_index=age_index,
                                distance_index=distance_index,
                                insurance=insurance,
                                source=source,
                                reminder_consent=reminder_consent,
                                prior_no_show_rate=prior_no_show_rate,
                                prior_no_show_count=prior_no_show_count,
                                prior_visits=prior_visits,
                                reliability=reliability,
                            )
                        ]
                    )
                )[0]
            )

            no_show = bool(rng.random() < probability)
            # A cancelled appointment is neither attended nor a no-show. It
            # only exists to give the booking-time history counters a signal.
            cancelled = (not no_show) and bool(rng.random() < 0.08 * (1.0 - reliability))

            current_day += float(rng.uniform(14.0, 120.0))
            start_at = _START + pd.Timedelta(days=current_day, hours=hour_of_day)
            booked_at = start_at - pd.Timedelta(days=lead_time_days)

            rows.append(
                {
                    "appointment_id": f"apt-{len(rows) + 1:06d}",
                    "patient_id": patient_id,
                    "booked_at": booked_at,
                    "start_at": start_at,
                    "lead_time_days": lead_time_days,
                    "hour_of_day": hour_of_day,
                    "weekday": weekday,
                    "appointment_type": appointment_type,
                    "provider_tenure_days": provider_tenure_days,
                    "prior_no_show_count": prior_no_show_count,
                    "prior_no_show_rate": round(prior_no_show_rate, 6),
                    "prior_cancellations": prior_cancellations,
                    "prior_visits": prior_visits,
                    "patient_age_band": _AGE_BANDS[age_index],
                    "source": source,
                    "insurance_self_pay": insurance,
                    "reminder_consent": reminder_consent,
                    "distance_band": _DISTANCE_BANDS[distance_index],
                    "is_new_patient": (
                        prior_visits == 0
                        and prior_no_show_count == 0
                        and prior_cancellations == 0
                    ),
                    TARGET: no_show,
                    "cancelled": cancelled,
                }
            )

            # Update the strictly-backwards history *after* the row is built.
            if no_show:
                prior_no_show_count += 1
            elif cancelled:
                prior_cancellations += 1
            else:
                prior_visits += 1

    frame = pd.DataFrame(rows[:size])
    frame["lead_time_band"] = frame["lead_time_days"].map(lead_time_band)
    frame = frame.sort_values(
        by=["patient_id", "start_at"], kind="mergesort"
    ).reset_index(drop=True)
    return frame


def no_show_rate(frame: pd.DataFrame) -> float:
    """Return the achieved (measured, not assumed) no-show rate."""

    if len(frame) == 0:
        return 0.0
    return round(float(frame[TARGET].mean()), 6)


def patient_overlap(left: pd.DataFrame, right: pd.DataFrame) -> set[str]:
    """Return the set of patient ids present in both frames."""

    return set(left["patient_id"]) & set(right["patient_id"])


def dataset_hash(frame: pd.DataFrame) -> str:
    """Return a stable SHA-256 over the training view of ``frame``.

    The training view is the id columns plus the canonical feature order plus
    the target, sorted by ``appointment_id``. Floats are rendered with a fixed
    precision so a platform-level float representation difference cannot change
    the hash.
    """

    columns = list(ID_COLUMNS) + list(FEATURE_NAMES) + [TARGET]
    view = frame[columns].copy()
    for name in ID_COLUMNS:
        if name in ("booked_at", "start_at"):
            view[name] = view[name].map(lambda value: pd.Timestamp(value).isoformat())
        else:
            view[name] = view[name].astype(str)
    for name in FEATURE_NAMES:
        if view[name].dtype == bool:
            view[name] = view[name].map(lambda value: "1" if value else "0")
        elif view[name].dtype.kind in {"f", "i", "u"}:
            view[name] = view[name].map(lambda value: f"{float(value):.6f}")
        else:
            view[name] = view[name].astype(str)
    view[TARGET] = view[TARGET].map(lambda value: "1" if value else "0")
    view = view.sort_values(by="appointment_id", kind="mergesort")
    payload = view.to_csv(index=False, lineterminator="\n")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def dataset_summary(frame: pd.DataFrame) -> dict[str, Any]:
    """Return the dataset description recorded in the metrics document."""

    return {
        "name": "synthetic",
        "n": int(len(frame)),
        "patients": int(frame["patient_id"].nunique()),
        "no_show_rate": no_show_rate(frame),
        "target_no_show_rate": TARGET_NO_SHOW_RATE,
        "hash": dataset_hash(frame),
        "disclosure": (
            "Synthetic data generated by ml.no_show.dataset. No real patient "
            "records are used; the model is not clinically validated."
        ),
    }


def feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return only the canonical feature columns, in order."""

    return frame[list(FEATURE_NAMES)].copy()


__all__ = [
    "build_synthetic_dataset",
    "dataset_hash",
    "dataset_summary",
    "feature_frame",
    "no_show_rate",
    "patient_overlap",
]
