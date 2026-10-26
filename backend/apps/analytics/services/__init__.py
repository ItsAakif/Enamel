"""Analytics services: metric compute functions (T133).

Each module owns one domain. The functions registered here are the *only*
implementations of their metric: a report endpoint calls
``registry.compute(key, clinic, from_date, to_date, location)`` and gets a
finished payload. Views therefore never contain a query and never invent a
number.

Every function takes clinic-local ``date`` bounds and resolves them through
``metrics.resolve_range``, so the inclusive-start / exclusive-end contract is
applied in exactly one place.
"""

from __future__ import annotations


def register_all() -> None:
    """Bind every domain metric to its compute function.

    Called from ``AnalyticsConfig.ready`` so the registry is populated for
    every entry point (views, exports, shell) without an import-order trap.
    """

    from . import appointments, recall, revenue, waitlist

    appointments.register_metrics()
    revenue.register_metrics()
    recall.register_metrics()
    waitlist.register_metrics()


__all__ = [
    "appointments",
    "export",
    "overview",
    "recall",
    "register_all",
    "revenue",
    "waitlist",
]
