"""Provider protocol and shared value objects (T123).

Everything an AI provider needs to speak is defined here so the OpenRouter
adapter, the deterministic mock, and the invocation service share one vocabulary.

Design rules
------------
* ``complete`` and ``embed`` are the only provider entry points.
* A provider never sees a database row: it receives plain messages and returns
  an :class:`AIResponse`. Redaction, budgeting and logging live in
  ``apps.ai.services``.
* Canonical hashing lives here too, because the mock's determinism and the
  stored ``request_hash``/``response_hash`` must use exactly the same function.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

DEFAULT_TIMEOUT_SECONDS = 30.0
VALID_ROLES = ("system", "user", "assistant", "tool")


# --------------------------------------------------------------------------- #
# Canonical hashing
# --------------------------------------------------------------------------- #


def canonical_json(value: Any) -> str:
    """Return a deterministic JSON string (sorted keys, no whitespace)."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Messages and responses
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Message:
    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


def coerce_message(value: Any) -> Message:
    """Accept a :class:`Message`, a ``{"role", "content"}`` dict, or a string."""

    if isinstance(value, Message):
        return value
    if isinstance(value, dict):
        role = str(value.get("role") or "user")
        return Message(role=role, content=str(value.get("content") or ""))
    return Message(role="user", content=str(value))


def coerce_messages(messages: Sequence[Any] | None) -> list[Message]:
    if not messages:
        return []
    return [coerce_message(item) for item in messages]


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: The billed cost when the provider echoes one (OpenRouter ``usage.cost``,
    #: USD). ``None`` when the API does not report it; callers must not fabricate.
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": dict(self.arguments)}


@dataclass
class AIResponse:
    text: str
    model: str
    usage: Usage = field(default_factory=Usage)
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    degraded: bool = False
    provider: str = ""
    #: The routing provider the API *actually* returned (OpenRouter may route a
    #: dynamic id such as ``openrouter/free`` to a concrete upstream model).
    native_provider: str = ""
    #: Wall-clock latency of the successful HTTP round trip.
    latency_ms: int = 0
    #: How many HTTP attempts (across retries and the fallback chain) it took.
    attempts: int = 1

    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def request_hash(
    messages: Sequence[Any] | None,
    *,
    model: str | None = None,
    tools: Any = None,
    response_schema: Any = None,
) -> str:
    """Hash the canonical request (messages + tools + schema + model)."""

    payload = {
        "messages": [m.as_dict() for m in coerce_messages(messages)],
        "model": model or "",
        "tools": tools or None,
        "response_schema": response_schema or None,
    }
    return sha256_hex(canonical_json(payload))


def response_hash(response: AIResponse) -> str:
    """Hash the model output. The content itself is never stored."""

    payload = {
        "text": response.text,
        "tool_calls": [call.as_dict() for call in response.tool_calls],
        "finish_reason": response.finish_reason,
    }
    return sha256_hex(canonical_json(payload))


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class AIProviderError(Exception):
    """Base class for provider failures."""

    code = "provider_error"
    retryable = False

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TransientProviderError(AIProviderError):
    """A failure that may succeed on retry (429, 5xx, timeout, bad body)."""

    code = "provider_transient"
    retryable = True


class ProviderRateLimited(TransientProviderError):
    code = "provider_rate_limited"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        #: Seconds the server asked the caller to wait (``Retry-After``
        #: header). ``None`` when absent -- the backoff then grows unbounded.
        self.retry_after = retry_after


class ProviderServerError(TransientProviderError):
    code = "provider_server_error"


class ProviderTimeout(TransientProviderError):
    code = "provider_timeout"


class ProviderMalformed(TransientProviderError):
    code = "provider_malformed"


class ProviderEmpty(TransientProviderError):
    code = "provider_empty"


class ProviderUnavailable(AIProviderError):
    """Raised when every model in the fallback chain is exhausted."""

    code = "provider_unavailable"
    retryable = False


class ProviderNotConfigured(AIProviderError):
    code = "provider_unavailable"
    retryable = False


class ProviderPolicyError(AIProviderError):
    """A configuration-policy refusal (the provider was never contacted)."""

    code = "provider_policy"
    retryable = False


class PaidModelBlocked(ProviderPolicyError):
    """The configured model has paid pricing and AI_ALLOW_PAID is not set.

    Raised by the free-model guard *before* any HTTP call is made, so a paid
    call can never be charged. It is deliberately not retryable and not part of
    the fallback chain: an operator who configured a paid model must see the
    refusal, not a silent substitute.
    """

    code = "paid_model_blocked"


# --------------------------------------------------------------------------- #
# Protocol
# --------------------------------------------------------------------------- #


@runtime_checkable
class AIProvider(Protocol):
    """Structural contract every provider adapter satisfies."""

    name: str

    def complete(
        self,
        messages: Sequence[Any],
        *,
        tools: Sequence[Any] | None = None,
        response_schema: dict[str, Any] | None = None,
        timeout: float | None = None,
        response_format: dict[str, Any] | None = None,
        tool_choice: Any = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        seed: int | None = None,
    ) -> AIResponse:  # pragma: no cover - protocol
        ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:  # pragma: no cover
        ...

    def health(self) -> dict[str, Any]:  # pragma: no cover - protocol
        ...


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "VALID_ROLES",
    "AIProvider",
    "AIProviderError",
    "AIResponse",
    "Message",
    "PaidModelBlocked",
    "ProviderEmpty",
    "ProviderMalformed",
    "ProviderNotConfigured",
    "ProviderPolicyError",
    "ProviderRateLimited",
    "ProviderServerError",
    "ProviderTimeout",
    "ProviderUnavailable",
    "ToolCall",
    "TransientProviderError",
    "Usage",
    "canonical_json",
    "coerce_message",
    "coerce_messages",
    "request_hash",
    "response_hash",
    "sha256_hex",
]
