"""Metric registry: data + function pairs, and the API payload shape (T133).

``metrics.SPECS`` is the data; ``registry`` binds each key to the callable that
computes it. Views and services call ``payload(...)`` and never write a metric
by hand, so every number carries its definition, its numerator and its
denominator by construction.

Empty-range rule
----------------
A metric whose denominator is zero is returned with ``value`` 0.0 (or 0 for a
count) and ``denominator_value`` 0 -- never ``null`` and never an invented
number. ``payload`` enforces that centrally so no service can forget it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .metrics import SPEC_BY_KEY, SPECS, MetricSpec

#: key -> callable(clinic, window, location) -> payload. Populated by the
#: service modules through :func:`register`; the compute functions live in
#: ``services/`` so no SQL is written in a view.
REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {}


class UnknownMetricError(KeyError):
    """Raised when a key is not in the registry."""


def register(key: str, compute: Callable[..., dict[str, Any]]) -> None:
    """Bind a metric key to its compute function (idempotent per key)."""

    if key not in SPEC_BY_KEY:
        raise UnknownMetricError(f"unknown metric key {key!r}")
    REGISTRY[key] = compute


def spec(key: str) -> MetricSpec:
    try:
        return SPEC_BY_KEY[key]
    except KeyError as exc:
        raise UnknownMetricError(f"unknown metric key {key!r}") from exc


def compute(key: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run the registered compute function for ``key``."""

    try:
        function = REGISTRY[key]
    except KeyError as exc:
        raise UnknownMetricError(f"metric {key!r} has no compute function") from exc
    return function(*args, **kwargs)


def payload(
    key: str,
    *,
    value: float | int,
    numerator_value: float | int,
    denominator_value: float | int,
    **extra: Any,
) -> dict[str, Any]:
    """Render a metric payload, enforcing the empty-range rule."""

    definition = spec(key)
    denominator = float(denominator_value)
    if denominator == 0:
        # A rate with no observations is 0, not null and not 1.
        safe_value: float | int = 0 if definition.unit == "count" else 0.0
    else:
        safe_value = value
    body: dict[str, Any] = {
        "key": definition.key,
        "label": definition.label,
        "unit": definition.unit,
        "definition": definition.definition,
        "numerator": definition.numerator,
        "denominator": definition.denominator,
        "value": _round(safe_value, definition.unit),
        "numerator_value": _round(numerator_value, definition.unit),
        "denominator_value": _round(denominator_value, definition.unit),
    }
    body.update(extra)
    return body


def _round(value: float | int, unit: str) -> float | int:
    if unit == "ratio":
        return round(float(value), 4)
    return int(value)


def metric_keys() -> list[str]:
    return [spec.key for spec in SPECS]


def collect(
    keys: list[str],
    clinic: Any,
    from_date: Any,
    to_date: Any,
    location: Any = None,
) -> list[dict[str, Any]]:
    """Compute several metrics, flattening per-currency lists.

    ``revenue.*`` metrics return one payload per currency, so callers get a flat
    list where a currency-bearing metric may appear more than once.
    """

    collected: list[dict[str, Any]] = []
    for key in keys:
        value = compute(key, clinic, from_date, to_date, location)
        collected.extend(value if isinstance(value, list) else [value])
    return collected


__all__ = [
    "REGISTRY",
    "UnknownMetricError",
    "collect",
    "compute",
    "metric_keys",
    "payload",
    "register",
    "spec",
]
