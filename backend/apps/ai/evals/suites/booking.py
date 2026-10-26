"""Booking outcome suite (T126).

Four cases through the real tool registry and the confirmation gate: a
successful booking, an unavailable slot, a duplicate attempt and a booking with
wrong details. Assertions are on outcomes and row counts, never wording.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from ..suites import load_dataset, result_row

SUITE = "booking"


def _engine(provider: Any):
    from apps.ai.voice.engine import ConversationEngine

    return ConversationEngine(provider=provider)


def _confirmed_call(world: Any, engine: Any):
    call = engine.start_call(
        clinic=world.clinic_a, patient=world.patient_a, started_by=world.user_a
    )
    call.identity_confirmed = True
    call.save(update_fields=["identity_confirmed", "updated_at"])
    return call


def _ctx(world: Any, call: Any):
    from apps.ai.tools.base import ToolContext

    return ToolContext(
        clinic=world.clinic_a,
        user=world.user_a,
        membership=world.membership_a,
        call=call,
        patient=world.patient_a,
    )


def _confirm_and_execute(registry: Any, ctx: Any, args: dict[str, Any]):
    """Drive the confirmation gate once and return the executed result."""

    first = registry.execute(ctx, "book_appointment", args)
    nonce = first.details.get("confirmation_nonce", "")
    if first.ok or not nonce:
        return first
    return registry.execute(
        ctx, "book_appointment", args, confirmed=True, confirmation_nonce=nonce
    )


def _success(world: Any, registry: Any, engine: Any) -> tuple[bool, str]:
    start = world.start_at
    call = _confirmed_call(world, engine)
    ctx = _ctx(world, call)
    args = {"appointment_type": world.type_a.code, "start_at": start.isoformat()}

    result = _confirm_and_execute(registry, ctx, args)
    if not result.ok:
        return False, f"status={result.status} code={result.error_code}"
    return result.result.get("duplicate") is False, f"duplicate={result.result.get('duplicate')}"


def _unavailable(world: Any, registry: Any, engine: Any) -> tuple[bool, str]:
    start = world.start_at + timedelta(hours=2)
    args = {"appointment_type": world.type_a.code, "start_at": start.isoformat()}

    first_call = _confirmed_call(world, engine)
    booked = _confirm_and_execute(registry, _ctx(world, first_call), args)
    if not booked.ok:
        return False, f"setup booking failed code={booked.error_code}"

    second_call = _confirmed_call(world, engine)
    conflict = _confirm_and_execute(registry, _ctx(world, second_call), args)
    passed = conflict.status == "rejected" and conflict.error_code == "slot_conflict"
    return passed, f"status={conflict.status} code={conflict.error_code}"


def _duplicate(world: Any, registry: Any, engine: Any) -> tuple[bool, str]:
    start = world.start_at + timedelta(hours=4)
    call = _confirmed_call(world, engine)
    ctx = _ctx(world, call)
    args = {"appointment_type": world.type_a.code, "start_at": start.isoformat()}

    first = _confirm_and_execute(registry, ctx, args)
    if not first.ok:
        return False, f"first booking failed code={first.error_code}"

    again = registry.execute(ctx, "book_appointment", args)
    nonce = again.details.get("confirmation_nonce", "")
    if not nonce:
        return False, "no confirmation nonce on the duplicate attempt"
    duplicate = registry.execute(
        ctx, "book_appointment", args, confirmed=True, confirmation_nonce=nonce
    )
    passed = duplicate.ok and duplicate.result.get("duplicate") is True
    return passed, f"duplicate={duplicate.result.get('duplicate') if duplicate.ok else duplicate.error_code}"


def _wrong_details(world: Any, registry: Any, engine: Any) -> tuple[bool, str]:
    start = world.start_at + timedelta(hours=6)
    call = _confirmed_call(world, engine)
    ctx = _ctx(world, call)
    args = {"appointment_type": "does-not-exist", "start_at": start.isoformat()}

    result = _confirm_and_execute(registry, ctx, args)
    passed = result.status == "rejected" and result.error_code == "not_found"
    return passed, f"status={result.status} code={result.error_code}"


CASES: dict[str, Callable[[Any, Any, Any], tuple[bool, str]]] = {
    "success": _success,
    "unavailable": _unavailable,
    "duplicate": _duplicate,
    "wrong_details": _wrong_details,
}


def run(world: Any, provider: Any, seed: int) -> list[dict[str, Any]]:
    from apps.ai.tools.registry import get_registry

    registry = get_registry()
    engine = _engine(provider)
    rows: list[dict[str, Any]] = []

    for case in load_dataset(SUITE):
        started = time.perf_counter()
        handler = CASES.get(case["case"])
        if handler is None:
            passed, reason = False, f"unknown case '{case['case']}'"
        else:
            try:
                passed, reason = handler(world, registry, engine)
            except Exception as exc:  # noqa: BLE001 - a suite failure is a case failure
                passed, reason = False, f"exception={type(exc).__name__}"
        latency_ms = int((time.perf_counter() - started) * 1000)
        rows.append(result_row(SUITE, case["id"], passed, reason, latency_ms=latency_ms))

    return rows
