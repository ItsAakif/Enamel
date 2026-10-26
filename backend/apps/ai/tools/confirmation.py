"""Confirmation gate helpers (T124).

A consequential tool needs two things at execution time:

1. ``confirmed=true`` in the same request, and
2. a prior assistant turn that *asked* for confirmation, carrying a nonce bound
   to the call.

The nonce lives on the call (``VoiceCall.confirmation_nonce``) and is
regenerated per request, so a confirmation captured from one call can never be
replayed against another call.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

CONFIRMATION_TEMPLATE = "Please confirm you would like me to {action}. Reply yes to continue."


def generate_nonce(call: Any) -> str:
    return hashlib.sha256(f"confirm:{getattr(call, 'id', '')}:{uuid.uuid4().hex}".encode()).hexdigest()[
        :32
    ]


def next_ordinal(call: Any) -> int:
    from ..models import VoiceTurn

    last = (
        VoiceTurn.all_objects.filter(call=call).order_by("-ordinal").values_list("ordinal", flat=True).first()
    )
    return int(last or 0) + 1


def ensure_confirmation_request(call: Any, tool_name: str, *, action: str | None = None) -> str:
    """Set (or reuse) the call's confirmation nonce and record the request turn.

    Returns the nonce the caller must echo back. Re-using an existing nonce for
    the same tool keeps the flow idempotent across repeated proposals.
    """

    from ..models import VoiceTurn

    if call.confirmation_nonce and call.confirmation_tool == tool_name:
        return call.confirmation_nonce

    nonce = generate_nonce(call)
    call.confirmation_nonce = nonce
    call.confirmation_tool = tool_name
    call.save(update_fields=["confirmation_nonce", "confirmation_tool", "updated_at"])

    prompt = CONFIRMATION_TEMPLATE.format(action=(action or tool_name).replace("_", " "))
    VoiceTurn.all_objects.create(
        clinic_id=call.clinic_id,
        call=call,
        role=VoiceTurn.Role.ASSISTANT,
        kind=VoiceTurn.Kind.CONFIRMATION_REQUEST,
        ordinal=next_ordinal(call),
        text=prompt,
    )
    return nonce


def clear_confirmation(call: Any) -> None:
    call.confirmation_nonce = ""
    call.confirmation_tool = ""
    call.save(update_fields=["confirmation_nonce", "confirmation_tool", "updated_at"])


__all__ = [
    "CONFIRMATION_TEMPLATE",
    "clear_confirmation",
    "ensure_confirmation_request",
    "generate_nonce",
    "next_ordinal",
]
