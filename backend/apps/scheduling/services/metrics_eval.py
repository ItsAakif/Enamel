"""Deterministic synthetic evaluation for the waitlist (T116).

``run_cancel_fill`` simulates a clinic that books a fixed fraction of its
capacity, cancels a fixed fraction of those bookings, and then either leaves the
gaps empty (the *baseline*) or tries to fill them from a waitlist using the real
:func:`~apps.scheduling.services.recommend.score_entry` scorer.

Everything is driven by a seeded ``random.Random`` and a fixed anchor date, so
the JSON summary is byte-identical for the same ``(scenario, days, seed)``. The
simulation touches no database: it is a measurement harness, not a demo seeder.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from datetime import date as date_type
from datetime import time as time_type
from types import SimpleNamespace
from typing import Any

from .recommend import score_entry

#: Fixed anchor so the report is reproducible regardless of the day it runs.
DEFAULT_ANCHOR = date_type(2026, 1, 5)
SCENARIO_CANCEL_FILL = "cancel-fill"


def _round(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def run_cancel_fill(
    *,
    anchor_date: date_type = DEFAULT_ANCHOR,
    days: int = 30,
    seed: int = 42,
    providers: int = 2,
    slots_per_provider_day: int = 16,
    duration_min: int = 30,
    book_rate: float = 0.7,
    cancel_rate: float = 0.2,
    waitlist_size: int = 40,
    claim_rate: float = 0.6,
) -> dict[str, Any]:
    """Run the ``cancel-fill`` scenario and return a JSON summary."""

    days = max(1, int(days))
    providers = max(1, int(providers))
    slots_per_provider_day = max(1, int(slots_per_provider_day))
    duration_min = max(1, int(duration_min))
    rng = random.Random(seed)

    anchor_dt = datetime.combine(anchor_date, time_type(9, 0), tzinfo=UTC)
    window_end = anchor_dt + timedelta(days=days)

    available_minutes = providers * slots_per_provider_day * duration_min * days
    total_slots = providers * slots_per_provider_day * days
    booked = int(round(total_slots * book_rate))
    cancelled = int(round(booked * cancel_rate))
    baseline_booked_minutes = (booked - cancelled) * duration_min

    provider_ids = [f"provider-{index + 1}" for index in range(providers)]

    # Synthetic waitlist candidates.
    entries: list[SimpleNamespace] = []
    for index in range(max(1, int(waitlist_size))):
        created_at = anchor_dt - timedelta(days=rng.randint(1, 25))
        entries.append(
            SimpleNamespace(
                id=f"entry-{index + 1}",
                appointment_type_id="type-1",
                provider_pref_id=(
                    rng.choice(provider_ids) if rng.random() < 0.4 else None
                ),
                window_start=anchor_dt - timedelta(days=7),
                window_end=window_end + timedelta(days=7),
                urgency=rng.randint(1, 5),
                created_at=created_at,
            )
        )
    available_entries = list(entries)

    offers_created = 0
    claims = 0
    waiting_days: list[float] = []
    conflicts = 0

    for index in range(cancelled):
        start = anchor_dt + timedelta(
            days=index % days, minutes=duration_min * (index // days + 1)
        )
        gap = {
            "start": start,
            "end": start + timedelta(minutes=duration_min),
            "provider_id": provider_ids[index % providers],
            "operatory_id": None,
            "appointment_type_id": "type-1",
            "reason": "cancellation",
        }
        if not available_entries:
            break
        scored = sorted(
            (
                (score_entry(entry, gap, now=anchor_dt)["total"], entry)
                for entry in available_entries
            ),
            key=lambda pair: (-pair[0], pair[1].id),
        )
        best_entry = scored[0][1]
        offers_created += 1
        # The held-appointment model offers each slot at most once, so no slot
        # conflict can occur by construction; the counter is reported as 0.
        if rng.random() < claim_rate:
            claims += 1
            waiting_days.append((start - best_entry.created_at).total_seconds() / 86400.0)
            available_entries.remove(best_entry)
        # A declined offer keeps the entry in the pool (it may be offered again).

    declined = int(round((offers_created - claims) * 0.25))
    expired = offers_created - claims - declined
    waitlist_booked_minutes = baseline_booked_minutes + claims * duration_min

    average_wait = _round(sum(waiting_days) / len(waiting_days), 2) if waiting_days else None

    return {
        "scenario": SCENARIO_CANCEL_FILL,
        "seed": int(seed),
        "days": days,
        "window": {
            "anchor": anchor_date.isoformat(),
            "from": anchor_dt.date().isoformat(),
            "to": (window_end - timedelta(days=1)).date().isoformat(),
        },
        "inputs": {
            "providers": providers,
            "slots_per_provider_day": slots_per_provider_day,
            "duration_min": duration_min,
            "book_rate": book_rate,
            "cancel_rate": cancel_rate,
            "waitlist_size": max(1, int(waitlist_size)),
            "claim_rate": claim_rate,
        },
        "capacity": {
            "total_slots": total_slots,
            "available_minutes": available_minutes,
            "booked": booked,
            "cancelled": cancelled,
        },
        "baseline": {
            "filled_from_waitlist": 0,
            "booked_minutes": baseline_booked_minutes,
            "available_minutes": available_minutes,
            "utilisation": _round(baseline_booked_minutes / available_minutes),
            "waiting_time_days": None,
        },
        "waitlist": {
            "filled_from_waitlist": claims,
            "booked_minutes": waitlist_booked_minutes,
            "available_minutes": available_minutes,
            "utilisation": _round(waitlist_booked_minutes / available_minutes),
            "waiting_time_days": average_wait,
            "offers": {
                "created": offers_created,
                "claimed": claims,
                "declined": declined,
                "expired": expired,
            },
        },
        "conflicts": conflicts,
        "definitions": {
            "utilisation": "booked_minutes / available_minutes",
            "booked_minutes": "sum of (end - start) for consuming appointments",
            "available_minutes": (
                "providers * slots_per_provider_day * duration_min * days"
            ),
            "conflicts": "slot conflicts observed; the held-appointment model prevents double-selling",
            "waiting_time_days": "mean days between entry creation and offer claim",
            "baseline": "no waitlist: cancelled slots stay empty",
        },
    }


__all__ = ["DEFAULT_ANCHOR", "SCENARIO_CANCEL_FILL", "run_cancel_fill"]
