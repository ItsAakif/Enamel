"""Invocation pipeline (T123).

One function, :func:`invoke`, is the only way application code talks to a
provider. It owns the cross-cutting rules so no caller can forget them:

* the prompt registry is consulted for the key/version (bodies stay in code);
* the clinic budget guard runs *before* the provider call;
* the request is hashed with the canonical hasher, the response with the
  response hasher;
* exactly one ``AIInvocation`` row is written per call -- metrics and hashes
  only, never prompt or completion text;
* a provider failure is re-raised as the typed ``provider_unavailable``.

Nothing here logs or stores content. ``AI_LOG_CONTENT`` is intentionally not
implemented in T123.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..models import AIInvocation
from ..prompts.registry import PromptSpec, get_prompt
from ..providers.base import (
    AIProvider,
    AIProviderError,
    AIResponse,
    Message,
    ProviderUnavailable,
    ToolCall,
    Usage,
    coerce_messages,
    request_hash,
    response_hash,
)
from ..providers.factory import get_provider
from .budget import BudgetExceeded, check_budget
from .cost import compute_cost_minor

CODE_BUDGET_EXCEEDED = "budget_exceeded"


@dataclass
class InvocationResult:
    answer: str
    model: str
    provider: str
    usage: Usage
    status: str
    degraded: bool = False
    citations: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_key: str = ""
    prompt_version: int = 0
    trace_id: str = ""
    invocation_id: str = ""


def build_messages(
    prompt: PromptSpec | None,
    user_message: str,
    *,
    history: Sequence[Any] | None = None,
) -> list[Message]:
    """Compose ``[system?, ...history, user]`` for one invocation."""

    messages: list[Message] = []
    if prompt is not None:
        messages.append(Message(role="system", content=prompt.body))
    messages.extend(coerce_messages(history))
    messages.append(Message(role="user", content=user_message))
    return messages


def _log_invocation(
    *,
    clinic: Any,
    user: Any,
    provider_name: str,
    model: str,
    purpose: str,
    prompt_key: str,
    prompt_version: int,
    status: str,
    latency_ms: int,
    usage: Usage,
    cost_minor: int,
    req_hash: str,
    resp_hash: str,
    error_code: str,
    trace_id: str,
) -> AIInvocation:
    return AIInvocation.objects.create(
        clinic=clinic,
        user=user,
        provider=provider_name,
        model=model,
        purpose=purpose or "",
        prompt_key=prompt_key or "",
        prompt_version=prompt_version or 0,
        status=status,
        latency_ms=latency_ms,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cost_minor=cost_minor,
        request_hash=req_hash,
        response_hash=resp_hash,
        error_code=error_code or "",
        trace_id=trace_id or "",
    )


def invoke(
    *,
    purpose: str,
    messages: Sequence[Any],
    clinic: Any = None,
    user: Any = None,
    provider: AIProvider | None = None,
    prompt_key: str | None = None,
    tools: Sequence[Any] | None = None,
    response_schema: dict[str, Any] | None = None,
    timeout: float | None = None,
    trace_id: str | None = None,
) -> InvocationResult:
    """Run one provider call and record it. Raises typed provider errors."""

    active_provider = provider or get_provider()
    provider_name = getattr(active_provider, "name", "unknown")
    model = getattr(active_provider, "model", "") or ""
    prompt = get_prompt(prompt_key) if prompt_key else None
    prompt_version = prompt.version if prompt is not None else 0
    resolved_key = prompt.key if prompt is not None else (prompt_key or "")
    trace = trace_id or uuid.uuid4().hex
    msgs = coerce_messages(messages)
    req_hash = request_hash(
        msgs, model=model, tools=tools, response_schema=response_schema
    )

    started = time.perf_counter()

    # Budget guard: refuse before touching the provider, but still record the
    # attempt so the block is auditable.
    try:
        check_budget(clinic)
    except BudgetExceeded as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        row = _log_invocation(
            clinic=clinic,
            user=user,
            provider_name=provider_name,
            model=model,
            purpose=purpose,
            prompt_key=resolved_key,
            prompt_version=prompt_version,
            status=AIInvocation.Status.ERROR,
            latency_ms=latency_ms,
            usage=Usage(),
            cost_minor=0,
            req_hash=req_hash,
            resp_hash="",
            error_code=CODE_BUDGET_EXCEEDED,
            trace_id=trace,
        )
        exc.trace_id = trace  # type: ignore[attr-defined]
        exc.invocation_id = str(row.id)  # type: ignore[attr-defined]
        raise

    response: AIResponse | None = None
    error: AIProviderError | None = None
    try:
        response = active_provider.complete(
            msgs,
            tools=tools,
            response_schema=response_schema,
            timeout=timeout,
        )
    except AIProviderError as exc:
        error = exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    usage = response.usage if response is not None else Usage()
    resp_hash = response_hash(response) if response is not None else ""
    cost_minor = compute_cost_minor(model, usage.prompt_tokens, usage.completion_tokens)
    status = AIInvocation.Status.OK if response is not None else AIInvocation.Status.ERROR
    error_code = "" if error is None else error.code

    row = _log_invocation(
        clinic=clinic,
        user=user,
        provider_name=provider_name,
        model=model,
        purpose=purpose,
        prompt_key=resolved_key,
        prompt_version=prompt_version,
        status=status,
        latency_ms=latency_ms,
        usage=usage,
        cost_minor=cost_minor,
        req_hash=req_hash,
        resp_hash=resp_hash,
        error_code=error_code,
        trace_id=trace,
    )

    if response is None:
        raise ProviderUnavailable(
            "The AI provider is unavailable.",
            status_code=503,
        ) from error

    return InvocationResult(
        answer=response.text,
        model=response.model or model,
        provider=provider_name,
        usage=usage,
        status=status,
        degraded=response.degraded,
        citations=[],
        tool_calls=list(response.tool_calls),
        prompt_key=resolved_key,
        prompt_version=prompt_version,
        trace_id=trace,
        invocation_id=str(row.id),
    )


__all__ = [
    "CODE_BUDGET_EXCEEDED",
    "InvocationResult",
    "build_messages",
    "invoke",
]
