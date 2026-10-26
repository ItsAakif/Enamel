"""Fixed configuration for the no-show pipeline (T131).

Everything here is a constant on purpose. Reproduction means "same seed and
same dataset hash -> same metrics hash", so nothing in the pipeline may read
wall-clock time, environment state or randomness that is not derived from the
seed.

Feature order is part of the contract: the training matrix, the persisted
artifact and the serving-side encoder all iterate ``FEATURE_NAMES`` in this
exact order. Reordering the list is a breaking change to every stored artifact.
"""

from __future__ import annotations

#: Default RNG seed. The task and ADR-010 pin this value.
DEFAULT_SEED = 42

#: Synthetic dataset size (the task requires >= 1,000 rows).
DEFAULT_DATASET_SIZE = 1200

#: Fraction of *patients* held out by ``GroupShuffleSplit``.
TEST_SIZE = 0.25

#: Fraction of the newest appointments held out as the secondary temporal
#: check. Computed after sorting by ``start_at``; the newest slice is the test
#: side, so no future row can inform a past prediction.
TEMPORAL_TEST_SIZE = 0.25

#: Number of equal-width probability bins in the calibration curve.
CALIBRATION_BINS = 10

#: The synthetic generator targets this no-show rate; the achieved rate is
#: measured and recorded in the metrics document (it is not assumed).
TARGET_NO_SHOW_RATE = 0.18

#: Target column name in the dataframe.
TARGET = "no_show"

#: Identifier columns (never features).
ID_COLUMNS = ("appointment_id", "patient_id", "booked_at", "start_at")

#: Subgroup columns used for the error analysis. They are *not* model inputs.
SUBGROUP_COLUMNS = ("appointment_type", "source", "lead_time_band")

#: Canonical feature order. See ``features.FEATURES`` for the per-feature
#: reason and kind.
FEATURE_NAMES = (
    "lead_time_days",
    "hour_of_day",
    "weekday",
    "appointment_type",
    "provider_tenure_days",
    "prior_no_show_count",
    "prior_no_show_rate",
    "prior_cancellations",
    "prior_visits",
    "patient_age_band",
    "source",
    "insurance_self_pay",
    "reminder_consent",
    "distance_band",
    "is_new_patient",
)

#: Categorical features and their fixed, ordered category lists. The integer
#: code of a value is its index in the list, so encoding is stable across runs
#: and identical between training and serving.
CATEGORICAL_FEATURES: dict[str, tuple[str, ...]] = {
    "appointment_type": ("checkup", "cleaning", "filling", "root_canal", "extraction"),
    "patient_age_band": ("0-17", "18-34", "35-49", "50-64", "65+"),
    "source": ("staff", "online", "ai", "phone"),
    "insurance_self_pay": ("insurance", "self_pay"),
    "distance_band": ("0-2km", "2-5km", "5-10km", "10km+"),
}

#: Boolean features (encoded as 0/1).
BOOLEAN_FEATURES = ("reminder_consent", "is_new_patient")

#: Numeric features (passed through unchanged).
NUMERIC_FEATURES = (
    "lead_time_days",
    "hour_of_day",
    "weekday",
    "provider_tenure_days",
    "prior_no_show_count",
    "prior_no_show_rate",
    "prior_cancellations",
    "prior_visits",
)

#: Lead-time bands used by the subgroup analysis.
LEAD_TIME_BANDS: tuple[tuple[str, float, float], ...] = (
    ("same_day", 0.0, 1.0),
    ("1-3d", 1.0, 4.0),
    ("4-14d", 4.0, 15.0),
    ("15d+", 15.0, float("inf")),
)

#: Threshold search grid (validation set only).
THRESHOLD_GRID = tuple(round(0.05 + 0.01 * i, 2) for i in range(91))  # 0.05 .. 0.95

#: Baseline algorithm names recorded in the registry.
BASELINE_PRIOR = "prior_probability"
BASELINE_LOGISTIC = "logistic_regression"

#: Candidate algorithm name recorded in the registry.
CANDIDATE_ALGORITHM = "hist_gradient_boosting_isotonic"

#: Registry status values (mirrors ``apps.ml.models.NoShowModel.Status``).
STATUS_CANDIDATE = "candidate"
STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"

#: Prediction decisions.
DECISION_HIGH_RISK = "high_risk"
DECISION_LOW_RISK = "low_risk"

#: Deterministic metric rounding. Six decimals is far finer than any clinical
#: meaning and removes platform-level float noise from the metrics hash.
METRIC_PRECISION = 6

#: Filenames inside the output directory.
METRICS_FILENAME = "noshow_metrics.json"

#: The disclosure string that must appear next to any score.
SYNTHETIC_DISCLOSURE = (
    "Experimental, assistive signal only. Trained on synthetic data. "
    "Not validated for clinical use and not a clinical judgement."
)


def artifact_filename(version: str) -> str:
    """Return the joblib artifact filename for ``version``."""

    return f"noshow_{version}.joblib"


__all__ = [
    "BASELINE_LOGISTIC",
    "BASELINE_PRIOR",
    "BOOLEAN_FEATURES",
    "CALIBRATION_BINS",
    "CANDIDATE_ALGORITHM",
    "CATEGORICAL_FEATURES",
    "DECISION_HIGH_RISK",
    "DECISION_LOW_RISK",
    "DEFAULT_DATASET_SIZE",
    "DEFAULT_SEED",
    "FEATURE_NAMES",
    "ID_COLUMNS",
    "LEAD_TIME_BANDS",
    "METRIC_PRECISION",
    "METRICS_FILENAME",
    "NUMERIC_FEATURES",
    "STATUS_ACTIVE",
    "STATUS_CANDIDATE",
    "STATUS_RETIRED",
    "SUBGROUP_COLUMNS",
    "SYNTHETIC_DISCLOSURE",
    "TARGET",
    "TARGET_NO_SHOW_RATE",
    "TEMPORAL_TEST_SIZE",
    "TEST_SIZE",
    "THRESHOLD_GRID",
    "artifact_filename",
]
