"""Structured-output validation with one repair attempt (T123).

The model is asked for JSON that matches a pydantic model. When the reply does
not parse or does not validate, the pipeline makes **one** repair attempt: it
re-sends the conversation with the invalid reply and a terse instruction to
return valid JSON only. A second failure raises :class:`AIOutputInvalid`, which
the caller turns into a safe refusal -- never a fabricated answer.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from ..providers.base import AIProviderError, AIResponse, Message, coerce_messages

REPAIR_INSTRUCTION = (
    "Your previous reply was not valid JSON for the required schema. "
    "Reply with valid JSON only and no explanation."
)


class AIOutputInvalid(AIProviderError):
    """The model did not return schema-valid JSON after the repair attempt."""

    code = "ai_output_invalid"
    retryable = False


def extract_json(text: str) -> Any:
    """Parse ``text`` as JSON, tolerating a fenced ```json block."""

    candidate = (text or "").strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
    try:
        return json.loads(candidate)
    except (TypeError, ValueError) as exc:
        raise AIOutputInvalid("The model did not return valid JSON.") from exc


def validate_payload[ModelT: BaseModel](payload: Any, model: type[ModelT]) -> ModelT:
    """Validate ``payload`` against ``model``; raise :class:`AIOutputInvalid`."""

    if not isinstance(payload, dict):
        raise AIOutputInvalid("The model did not return a JSON object.")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise AIOutputInvalid("The model output did not match the required schema.") from exc


def validate_text[ModelT: BaseModel](text: str, model: type[ModelT]) -> ModelT:
    return validate_payload(extract_json(text), model)


def complete_structured[ModelT: BaseModel](
    provider: Any,
    messages: Sequence[Any],
    model: type[ModelT],
    *,
    tools: Sequence[Any] | None = None,
    timeout: float | None = None,
    repair_attempts: int = 1,
) -> tuple[ModelT, AIResponse]:
    """Call ``provider`` and return ``(validated_model, final_response)``.

    ``repair_attempts`` defaults to one, matching the spec; a second invalid
    reply raises the last :class:`AIOutputInvalid`.
    """

    schema = model.model_json_schema()
    conversation = coerce_messages(messages)
    response = provider.complete(
        conversation,
        tools=tools,
        response_schema=schema,
        timeout=timeout,
    )
    try:
        return validate_text(response.text, model), response
    except AIOutputInvalid as first_error:
        last_error = first_error
        current = response

    attempts = max(0, int(repair_attempts))
    for _ in range(attempts):
        repair_messages = [
            *conversation,
            Message(role="assistant", content=current.text),
            Message(role="user", content=REPAIR_INSTRUCTION),
        ]
        current = provider.complete(
            repair_messages,
            tools=tools,
            response_schema=schema,
            timeout=timeout,
        )
        try:
            return validate_text(current.text, model), current
        except AIOutputInvalid as exc:
            last_error = exc
    raise last_error


__all__ = [
    "AIOutputInvalid",
    "REPAIR_INSTRUCTION",
    "complete_structured",
    "extract_json",
    "validate_payload",
    "validate_text",
]
