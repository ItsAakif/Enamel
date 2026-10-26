"""Booking-time feature registry and encoding (T131).

Every feature carries the reason it is legitimate to use. The rule (ADR-010) is
simple: a feature may only use information that is *known when the appointment
is booked*. Anything recorded later -- whether the patient showed up, the
cancellation reason, the reminder delivery outcome, the encounter -- is leakage
and is deliberately absent.

The two riskiest features are the patient history counters
(``prior_no_show_count`` / ``prior_no_show_rate``). They are computed over the
patient's *earlier* appointments only, strictly ordered by start time, and
``tests/test_dataset.py`` asserts that a future appointment cannot change an
earlier row's counter.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .config import (
    BOOLEAN_FEATURES,
    CATEGORICAL_FEATURES,
    FEATURE_NAMES,
    NUMERIC_FEATURES,
)


@dataclass(frozen=True)
class Feature:
    """One documented model input."""

    name: str
    kind: str
    description: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "reason": self.reason,
        }


#: The registry. Order matches ``config.FEATURE_NAMES``.
FEATURES: tuple[Feature, ...] = (
    Feature(
        name="lead_time_days",
        kind="numeric",
        description="Days between the booking instant and the appointment start.",
        reason=(
            "Known at booking time; long lead times are a well-documented "
            "no-show driver and cannot leak the outcome."
        ),
    ),
    Feature(
        name="hour_of_day",
        kind="numeric",
        description="Appointment start hour in the clinic timezone (0-23).",
        reason=(
            "The chosen slot is fixed at booking time; early/late slots have "
            "different attendance behaviour."
        ),
    ),
    Feature(
        name="weekday",
        kind="numeric",
        description="Appointment weekday in the clinic timezone (Monday=0).",
        reason="Fixed at booking time; captures routine-of-week effects.",
    ),
    Feature(
        name="appointment_type",
        kind="categorical",
        description="Service code of the booked appointment type.",
        reason=(
            "Chosen at booking time; procedure mix correlates with perceived "
            "urgency (e.g. emergency extraction vs routine cleaning)."
        ),
    ),
    Feature(
        name="provider_tenure_days",
        kind="numeric",
        description="Days the provider had been registered at the clinic when "
        "the appointment was booked.",
        reason=(
            "Provider row exists at booking time; a proxy for the provider's "
            "own patient familiarity, without using any later outcome."
        ),
    ),
    Feature(
        name="prior_no_show_count",
        kind="numeric",
        description="Patient's no-show count over strictly earlier appointments.",
        reason=(
            "History as of booking time only (time-ordered window). A patient "
            "who missed past visits is more likely to miss the next one; using "
            "later rows would leak the target."
        ),
    ),
    Feature(
        name="prior_no_show_rate",
        kind="numeric",
        description="prior_no_show_count / max(prior_no_show_count + prior_visits "
        "+ prior_cancellations, 1) as of booking.",
        reason="Normalised version of the same booking-time history.",
    ),
    Feature(
        name="prior_cancellations",
        kind="numeric",
        description="Patient's cancellation count over earlier appointments.",
        reason="Booking-time history; cancellation and no-show share drivers.",
    ),
    Feature(
        name="prior_visits",
        kind="numeric",
        description="Patient's completed visit count over earlier appointments.",
        reason=(
            "Booking-time history; a proxy for engagement. Counted strictly "
            "before the current appointment."
        ),
    ),
    Feature(
        name="patient_age_band",
        kind="categorical",
        description="Patient age band derived from date_of_birth at booking.",
        reason="Date of birth is recorded before booking and is immutable.",
    ),
    Feature(
        name="source",
        kind="categorical",
        description="Booking channel (staff, online, ai, phone).",
        reason=(
            "Recorded when the appointment is created; self-service channels "
            "have different commitment levels."
        ),
    ),
    Feature(
        name="insurance_self_pay",
        kind="categorical",
        description="Whether the visit is insured or self-pay.",
        reason=(
            "Payment route is decided at booking time; known before the visit "
            "and independent of attendance."
        ),
    ),
    Feature(
        name="reminder_consent",
        kind="boolean",
        description="Patient had appointment-reminder consent at booking.",
        reason=(
            "Consent is a record written before the visit (messaging.Consent). "
            "Whether the reminder was *delivered* is post-booking and excluded."
        ),
    ),
    Feature(
        name="distance_band",
        kind="categorical",
        description="Travel distance band between patient and clinic.",
        reason=(
            "Static geography known at booking time; long travel is a "
            "documented attendance barrier."
        ),
    ),
    Feature(
        name="is_new_patient",
        kind="boolean",
        description="True when the patient has no earlier terminal appointment.",
        reason=(
            "Cold-start flag derived from booking-time history; makes the "
            "zero priors explicit instead of a silent default."
        ),
    ),
)

FEATURE_BY_NAME: Mapping[str, Feature] = {feature.name: feature for feature in FEATURES}

#: Human-readable definitions, used by the API and the README.
FEATURE_DEFINITIONS: tuple[dict[str, str], ...] = tuple(
    feature.as_dict() for feature in FEATURES
)


def validate_registry() -> None:
    """Assert the registry, the kind lists and the canonical order agree."""

    names = [feature.name for feature in FEATURES]
    if names != list(FEATURE_NAMES):
        raise AssertionError(
            "features.FEATURES order must match config.FEATURE_NAMES"
        )
    kinds = set(NUMERIC_FEATURES) | set(CATEGORICAL_FEATURES) | set(BOOLEAN_FEATURES)
    if kinds != set(names):
        missing = sorted(set(names) - kinds)
        extra = sorted(kinds - set(names))
        raise AssertionError(
            f"feature kind lists do not match the registry: missing={missing} extra={extra}"
        )
    if set(NUMERIC_FEATURES) & set(CATEGORICAL_FEATURES):
        raise AssertionError("a feature cannot be both numeric and categorical")


def encode_value(name: str, value: Any) -> float:
    """Encode one raw feature value to a float in a stable way.

    Numeric features pass through. Booleans become 0/1. Categoricals become the
    index of the value in the fixed category list; an unknown category raises
    ``KeyError`` rather than being coerced to 0, because a silent code would
    change the meaning of the vector.
    """

    if name in NUMERIC_FEATURES:
        if value is None:
            raise ValueError(f"numeric feature {name!r} is missing")
        return float(value)
    if name in BOOLEAN_FEATURES:
        if value is None:
            raise ValueError(f"boolean feature {name!r} is missing")
        if isinstance(value, str):
            return 1.0 if value.strip().lower() in {"1", "true", "yes"} else 0.0
        return 1.0 if value else 0.0
    categories = CATEGORICAL_FEATURES.get(name)
    if categories is None:
        raise KeyError(f"unknown feature {name!r}")
    text = "" if value is None else str(value)
    if text not in categories:
        raise KeyError(f"unknown category {text!r} for feature {name!r}")
    return float(categories.index(text))


def encode_record(record: Mapping[str, Any]) -> list[float]:
    """Encode a complete feature record in canonical order."""

    return [encode_value(name, record.get(name)) for name in FEATURE_NAMES]


def missing_features(record: Mapping[str, Any]) -> list[str]:
    """Return the canonical-order list of absent or unusable feature names."""

    missing: list[str] = []
    for name in FEATURE_NAMES:
        if name not in record:
            missing.append(name)
            continue
        value = record[name]
        if value is None:
            missing.append(name)
            continue
        if name in CATEGORICAL_FEATURES and str(value) not in CATEGORICAL_FEATURES[name]:
            missing.append(name)
    return missing


def lead_time_band(days: float) -> str:
    """Bucket a lead time in days into the documented subgroup band."""

    from .config import LEAD_TIME_BANDS

    for label, low, high in LEAD_TIME_BANDS:
        if low <= float(days) < high:
            return label
    return LEAD_TIME_BANDS[-1][0]


def feature_names() -> list[str]:
    """The canonical feature order as a plain list."""

    return list(FEATURE_NAMES)


def kind_of(name: str) -> str:
    """Return the declared kind of ``name`` (raises for unknown names)."""

    return FEATURE_BY_NAME[name].kind


def all_feature_names() -> Iterable[str]:
    return iter(FEATURE_NAMES)


__all__ = [
    "FEATURE_BY_NAME",
    "FEATURE_DEFINITIONS",
    "FEATURES",
    "Feature",
    "all_feature_names",
    "encode_record",
    "encode_value",
    "feature_names",
    "kind_of",
    "lead_time_band",
    "missing_features",
    "validate_registry",
]
