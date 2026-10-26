"""Read projections for the voice console (T124).

Everything a screen needs, and nothing it does not: transcript text stays in the
``turns`` array (it is a policy field), tool calls expose args/results, and no
projection ever reaches into another clinic.
"""

from __future__ import annotations

from typing import Any


def turn_resource(turn: Any) -> dict[str, Any]:
    return {
        "id": str(turn.id),
        "role": turn.role,
        "kind": turn.kind,
        "ordinal": turn.ordinal,
        "text": turn.text,
        "confidence": turn.confidence,
        "created_at": turn.created_at,
    }


def tool_call_resource(tool_call: Any) -> dict[str, Any]:
    return {
        "id": str(tool_call.id),
        "tool": tool_call.tool,
        "status": tool_call.status,
        "confirmed": tool_call.confirmed,
        "args_json": tool_call.args_json,
        "result_json": tool_call.result_json,
        "created_at": tool_call.created_at,
    }


def call_resource(call: Any, *, include_children: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(call.id),
        "mode": call.mode,
        "direction": call.direction,
        "adapter": call.adapter,
        "status": call.status,
        "patient_id": str(call.patient_id) if call.patient_id else None,
        "recording_consent": call.recording_consent,
        "recording_key": call.recording_key or None,
        "identity_confirmed": call.identity_confirmed,
        "summary": call.summary,
        "outcome": call.outcome,
        "started_at": call.started_at,
        "ended_at": call.ended_at,
        "latency_ms": call.latency_ms,
        "cost_minor": call.cost_minor,
        "trace_id": call.trace_id,
        "caller_phone_hash": call.caller_phone_hash,
    }
    if include_children:
        payload["turns"] = [
            turn_resource(turn) for turn in call.turns.all().order_by("ordinal", "created_at")
        ]
        payload["tool_calls"] = [
            tool_call_resource(item) for item in call.tool_calls.all().order_by("created_at")
        ]
    return payload


def tool_invocation_resource(invocation: Any) -> dict[str, Any]:
    return {
        "id": str(invocation.id),
        "tool": invocation.tool,
        "status": invocation.status,
        "error_code": invocation.error_code,
        "latency_ms": invocation.latency_ms,
        "args_hash": invocation.args_hash,
        "result_json": invocation.result_json,
        "created_at": invocation.created_at,
    }


__all__ = [
    "call_resource",
    "tool_call_resource",
    "tool_invocation_resource",
    "turn_resource",
]
