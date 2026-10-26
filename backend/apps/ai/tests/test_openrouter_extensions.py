"""OpenRouter adapter extensions: payload shape, telemetry and the free-model guard.

Driven through ``httpx.MockTransport``; no test opens a socket (pytest-socket).
"""

from __future__ import annotations

import json

import httpx
import pytest

from apps.ai.providers.base import (
    AIProvider,
    PaidModelBlocked,
    ProviderServerError,
    request_hash,
)
from apps.ai.providers.factory import get_provider
from apps.ai.providers.openrouter import OpenRouterProvider, pricing_of

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"


def _catalogue_body(entries: list[dict]) -> dict:
    return {"data": entries}


def _entry(model_id: str, *, prompt: float = 0.0, completion: float = 0.0, tools: bool = True, structured: bool = True) -> dict:
    params = []
    if tools:
        params.append("tools")
    if structured:
        params.extend(["response_format", "structured_outputs"])
    return {
        "id": model_id,
        "name": model_id,
        "pricing": {"prompt": str(prompt), "completion": str(completion)},
        "supported_parameters": params or ["temperature"],
    }


def _completion(
    text: str = "hello",
    model: str = "requested/model",
    *,
    routed_model: str | None = None,
    provider_name: str = "ProviderX",
    cost: float | None = 0.0,
) -> httpx.Response:
    body = {
        "model": routed_model or model,
        "provider": provider_name,
        "choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, **({"cost": cost} if cost is not None else {})},
    }
    return httpx.Response(200, json=body)


def _router(catalogue_entries: list[dict], chat: list[httpx.Response | Exception]):
    """MockTransport serving /models from ``catalogue_entries`` and a chat script."""

    state = {"chat_calls": 0, "model_calls": 0}
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            state["model_calls"] += 1
            return httpx.Response(200, json=_catalogue_body(catalogue_entries))
        assert request.url.path.endswith("/chat/completions")
        seen.append(json.loads(request.content.decode("utf-8")))
        index = state["chat_calls"]
        state["chat_calls"] += 1
        item = chat[index] if index < len(chat) else chat[-1]
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.MockTransport(handler), state, seen


def _provider(transport, **kwargs) -> OpenRouterProvider:
    defaults = dict(
        api_key="test-key",
        model="requested/model",
        max_retries=2,
        sleep=lambda seconds: None,
    )
    defaults.update(kwargs)
    return OpenRouterProvider(transport=transport, **defaults)


# --------------------------------------------------------------------------- #


def test_payload_shape_json_schema():
    transport, _state, seen = _router([_entry("requested/model")], [_completion()])
    provider = _provider(transport)
    provider.complete(
        [{"role": "user", "content": "hi"}],
        response_format={"type": "json_schema", "json_schema": {"name": "r", "schema": {}}},
        temperature=0.2,
        max_tokens=64,
        seed=1234,
    )
    payload = seen[0]
    assert payload["model"] == "requested/model"
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 64
    assert payload["seed"] == 1234


def test_payload_shape_tools_and_tool_choice():
    transport, _state, seen = _router([_entry("requested/model")], [_completion()])
    provider = _provider(transport)
    tools = [{"type": "function", "function": {"name": "check_availability"}}]
    provider.complete(
        [{"role": "user", "content": "hi"}],
        tools=tools,
        tool_choice="auto",
    )
    payload = seen[0]
    assert payload["tools"] == tools
    assert payload["tool_choice"] == "auto"


def test_response_schema_keeps_json_object_for_backcompat():
    transport, _state, seen = _router([_entry("requested/model")], [_completion()])
    provider = _provider(transport)
    provider.complete(
        [{"role": "user", "content": "hi"}],
        response_schema={"type": "object"},
    )
    assert seen[0]["response_format"] == {"type": "json_object"}


def test_actual_model_native_provider_and_cost_captured():
    transport, _state, _seen = _router(
        [_entry("requested/model")],
        [_completion(model="requested/model", routed_model="routed/free-7b", provider_name="ProviderX", cost=0.0)],
    )
    provider = _provider(transport)
    response = provider.complete([{"role": "user", "content": "hi"}])
    assert response.model == "routed/free-7b"  # the *actual* returned model
    assert response.native_provider == "ProviderX"
    assert response.usage.cost_usd == 0.0
    assert response.usage.prompt_tokens == 10
    assert response.usage.completion_tokens == 5


def test_latency_and_attempt_count_recorded():
    transport, _state, _seen = _router(
        [_entry("requested/model")],
        [
            httpx.Response(429, json={}),
            _completion("recovered"),
        ],
    )
    provider = _provider(transport)
    response = provider.complete([{"role": "user", "content": "hi"}])
    assert response.text == "recovered"
    assert response.attempts == 2  # one retry after the 429
    assert response.latency_ms >= 0


def test_attempts_counts_across_fallback_chain():
    transport, _state, _seen = _router(
        [_entry("requested/model"), _entry("fallback/model")],
        [
            httpx.Response(500, json={}),
            httpx.Response(500, json={}),
            httpx.Response(500, json={}),
            _completion("fallback answer"),
        ],
    )
    provider = _provider(transport, fallback_models=["fallback/model"])
    response = provider.complete([{"role": "user", "content": "hi"}])
    assert response.attempts == 4
    assert provider.requests == ["requested/model"] * 3 + ["fallback/model"]


# --------------------------------------------------------------------------- #
# Free-model guard
# --------------------------------------------------------------------------- #


def test_guard_refuses_paid_model_before_any_chat_call():
    transport, state, seen = _router(
        [_entry("requested/model", prompt=0.15, completion=0.6)],
        [_completion("should never happen")],
    )
    provider = _provider(transport, free_guard=True)
    with pytest.raises(PaidModelBlocked) as exc_info:
        provider.complete([{"role": "user", "content": "hi"}])
    assert "requested/model" in str(exc_info.value)
    assert state["model_calls"] == 1
    assert state["chat_calls"] == 0  # the paid call never happened
    assert not seen


def test_guard_allows_free_model():
    transport, state, _seen = _router(
        [_entry("requested/model")], [_completion("free ok")]
    )
    provider = _provider(transport, free_guard=True)
    response = provider.complete([{"role": "user", "content": "hi"}])
    assert response.text == "free ok"
    assert state["chat_calls"] == 1


def test_guard_unresolved_model_is_not_blocked_here():
    transport, _state, _seen = _router(
        [_entry("other/model")], [_completion("proceeds")]
    )
    provider = _provider(transport, free_guard=True)
    response = provider.complete([{"role": "user", "content": "hi"}])
    assert response.text == "proceeds"


def test_guard_not_armed_by_default_on_direct_construction():
    transport, state, _seen = _router(
        [_entry("requested/model", prompt=3.0, completion=15.0)], [_completion("proceeds")]
    )
    provider = _provider(transport)
    response = provider.complete([{"role": "user", "content": "hi"}])
    assert response.text == "proceeds"


def test_allow_paid_bypasses_guard():
    transport, _state, _seen = _router(
        [_entry("requested/model", prompt=3.0, completion=15.0)], [_completion("paid ok")]
    )
    provider = _provider(transport, free_guard=True, allow_paid=True)
    response = provider.complete([{"role": "user", "content": "hi"}])
    assert response.text == "paid ok"


def test_guard_refusal_is_not_part_of_the_fallback_chain():
    transport, _state, _seen = _router(
        [
            _entry("requested/model", prompt=1.0),
            _entry("fallback/model"),  # free fallback configured
        ],
        [_completion("should never happen")],
    )
    provider = _provider(transport, free_guard=True, fallback_models=["fallback/model"])
    with pytest.raises(PaidModelBlocked):
        provider.complete([{"role": "user", "content": "hi"}])


def test_pricing_of_parses_mixed_types():
    assert pricing_of({"pricing": {"prompt": "0.0000015", "completion": 2}}) == (0.0000015, 2.0)
    assert pricing_of({"pricing": {}}) == (0.0, 0.0)
    assert pricing_of({}) == (0.0, 0.0)


# --------------------------------------------------------------------------- #
# Factory wiring
# --------------------------------------------------------------------------- #


def test_factory_arms_free_guard_by_default(settings):
    settings.AI_PROVIDER = "openrouter"
    settings.AI_MODEL = "requested/model"
    settings.OPENROUTER_API_KEY = "test-key"
    entries = [_entry("requested/model", prompt=0.2, completion=0.8)]
    transport, _state, _seen = _router(entries, [_completion("nope")])

    provider = get_provider("openrouter", transport=transport)
    assert isinstance(provider, OpenRouterProvider)
    assert provider.free_guard is True
    assert provider.allow_paid is False
    with pytest.raises(PaidModelBlocked):
        provider.complete([{"role": "user", "content": "hi"}])


def test_factory_allow_paid_opt_in(settings):
    settings.AI_PROVIDER = "openrouter"
    settings.AI_MODEL = "requested/model"
    settings.OPENROUTER_API_KEY = "test-key"
    settings.AI_ALLOW_PAID = True
    entries = [_entry("requested/model", prompt=0.2, completion=0.8)]
    transport, _state, _seen = _router(entries, [_completion("paid ok")])

    provider = get_provider("openrouter", transport=transport)
    assert provider.allow_paid is True
    assert provider.free_guard is False
    assert provider.complete([{"role": "user", "content": "hi"}]).text == "paid ok"


def test_factory_fallback_model_singular_precedes_chain(settings):
    settings.AI_PROVIDER = "openrouter"
    settings.AI_MODEL = "a/model"
    settings.OPENROUTER_API_KEY = "test-key"
    settings.AI_FALLBACK_MODEL = "b/model"
    settings.AI_FALLBACK_MODELS = ["b/model", "c/model"]
    provider = get_provider("openrouter")
    assert provider.model == "a/model"
    assert provider.fallback_models == ["b/model", "c/model"]
    assert isinstance(provider, AIProvider)  # the protocol is still structural


def test_guard_catalogue_fetch_failure_surfaces_typed_error():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(500, json={})

    transport = httpx.MockTransport(handler)
    provider = _provider(transport, free_guard=True)
    with pytest.raises(ProviderServerError):
        provider.complete([{"role": "user", "content": "hi"}])


def test_request_hash_still_canonical():
    assert request_hash([], model="m") == request_hash([], model="m")


def test_retry_after_is_honoured_and_never_shortened():
    transport, state, _seen = _router(
        [_entry("requested/model")],
        [httpx.Response(429, json={}, headers={"Retry-After": "22"}), _completion()],
    )
    sleeps: list[float] = []
    provider = _provider(transport, sleep=sleeps.append)
    response = provider.complete([{"role": "user", "content": "hi"}])
    assert response.text == "hello"
    assert state["chat_calls"] == 2
    assert response.attempts == 2
    # Honoured exactly: >= the requested 22 s (jitter is added on top).
    assert sleeps and sleeps[0] >= 22.0


def test_rate_limit_without_retry_after_uses_longer_backoff():
    transport, _state, _seen = _router(
        [_entry("requested/model")],
        [httpx.Response(429, json={}), _completion()],
    )
    sleeps: list[float] = []
    provider = _provider(transport, sleep=sleeps.append)
    response = provider.complete([{"role": "user", "content": "hi"}])
    assert response.text == "hello"
    # Backoff starts at BACKOFF_BASE_SECONDS (5 s) now, not sub-second.
    from apps.ai.providers.openrouter import BACKOFF_BASE_SECONDS

    assert sleeps and sleeps[0] >= BACKOFF_BASE_SECONDS * 0.8
