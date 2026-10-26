"""Provider tests (T123): determinism, faults, retries and fallback ordering.

No test opens a socket: the mock provider is offline and the OpenRouter adapter
is driven through an ``httpx.MockTransport`` injected at construction.
"""

from __future__ import annotations

import json

import httpx
import pytest

from apps.ai.providers.base import (
    ProviderEmpty,
    ProviderMalformed,
    ProviderRateLimited,
    ProviderServerError,
    ProviderTimeout,
    ProviderUnavailable,
    request_hash,
    response_hash,
)
from apps.ai.providers.factory import get_provider
from apps.ai.providers.mock import MockProvider, schema_value
from apps.ai.providers.openrouter import OpenRouterProvider

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


def _json_response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


def _completion(text: str = "hello", model: str = "test/model") -> httpx.Response:
    return _json_response(
        200,
        {
            "model": model,
            "choices": [
                {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


def _transport(sequence):
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        index = state["n"]
        state["n"] += 1
        item = sequence[index] if index < len(sequence) else sequence[-1]
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.MockTransport(handler), state


def _provider(transport, **kwargs) -> OpenRouterProvider:
    defaults = dict(
        api_key="test-key",
        model="primary/model",
        fallback_models=["fallback/model"],
        max_retries=2,
        sleep=lambda _seconds: None,
    )
    defaults.update(kwargs)
    return OpenRouterProvider(transport=transport, **defaults)


# --------------------------------------------------------------------------- #
# Mock provider determinism
# --------------------------------------------------------------------------- #


def test_mock_is_deterministic_for_same_request(mock_provider):
    messages = [{"role": "user", "content": "What are your opening hours?"}]
    first = mock_provider.complete(messages)
    second = mock_provider.complete(messages)
    assert first.text == second.text
    assert first.usage == second.usage
    assert request_hash(messages, model=mock_provider.model) == request_hash(
        messages, model=mock_provider.model
    )


def test_mock_differs_for_different_request(mock_provider):
    a = mock_provider.complete([{"role": "user", "content": "one"}])
    b = mock_provider.complete([{"role": "user", "content": "two"}])
    assert a.text != b.text


def test_mock_returns_schema_valid_json():
    from pydantic import BaseModel, ConfigDict

    class Reply(BaseModel):
        model_config = ConfigDict(extra="forbid")
        answer: str
        score: int

    provider = MockProvider()
    response = provider.complete(
        [{"role": "user", "content": "structured please"}],
        response_schema=Reply.model_json_schema(),
    )
    payload = json.loads(response.text)
    assert set(payload) == {"answer", "score"}
    assert isinstance(payload["score"], int)


def test_mock_returns_tool_calls():
    provider = MockProvider()
    tools = [{"type": "function", "function": {"name": "check_availability"}}]
    response = provider.complete(
        [
            {
                "role": "user",
                "content": json.dumps(
                    {"tool_call": {"name": "check_availability", "arguments": {"date": "2026-01-05"}}}
                ),
            }
        ],
        tools=tools,
    )
    assert response.has_tool_calls()
    assert response.tool_calls[0].name == "check_availability"
    assert response.tool_calls[0].arguments == {"date": "2026-01-05"}


def test_mock_ignores_unknown_tool():
    provider = MockProvider()
    tools = [{"type": "function", "function": {"name": "check_availability"}}]
    response = provider.complete(
        [{"role": "user", "content": json.dumps({"tool_call": {"name": "delete_everything"}})}],
        tools=tools,
    )
    assert response.tool_calls == []


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("429", ProviderRateLimited),
        ("500", ProviderServerError),
        ("timeout", ProviderTimeout),
        ("malformed", ProviderMalformed),
        ("empty", ProviderEmpty),
    ],
)
def test_mock_faults_raise(fault, expected):
    provider = MockProvider(faults=[fault])
    with pytest.raises(expected):
        provider.complete([{"role": "user", "content": "hi"}])


def test_mock_fault_then_success():
    provider = MockProvider(faults=["429"])
    with pytest.raises(ProviderRateLimited):
        provider.complete([{"role": "user", "content": "hi"}])
    response = provider.complete([{"role": "user", "content": "hi"}])
    assert response.text


def test_mock_invalid_json_returns_bad_content():
    provider = MockProvider(faults=["invalid_json"])
    response = provider.complete([{"role": "user", "content": "hi"}])
    assert not response.text.startswith("{")


def test_schema_value_handles_enums_and_arrays():
    schema = {
        "type": "object",
        "required": ["kind", "tags"],
        "properties": {
            "kind": {"type": "string", "enum": ["a", "b"]},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    }
    value = schema_value(schema, "abcdef0123456789")
    assert value["kind"] == "a"
    assert isinstance(value["tags"], list)


def test_mock_embed_is_deterministic(mock_provider):
    first = mock_provider.embed(["alpha", "beta"])
    second = mock_provider.embed(["alpha", "beta"])
    assert first == second
    assert len(first[0]) == mock_provider.embedding_dim


# --------------------------------------------------------------------------- #
# OpenRouter adapter
# --------------------------------------------------------------------------- #


def test_openrouter_200():
    transport, state = _transport([_completion("hi there")])
    provider = _provider(transport)
    response = provider.complete([{"role": "user", "content": "hi"}], timeout=5)
    assert response.text == "hi there"
    assert response.usage.prompt_tokens == 10
    assert provider.requests == ["primary/model"]
    assert state["n"] == 1


def test_openrouter_429_retries_then_succeeds():
    transport, state = _transport(
        [_json_response(429, {}), _json_response(429, {}), _completion("recovered")]
    )
    provider = _provider(transport)
    response = provider.complete([{"role": "user", "content": "hi"}])
    assert response.text == "recovered"
    assert provider.requests == ["primary/model", "primary/model", "primary/model"]
    assert state["n"] == 3


def test_openrouter_500_exhausts_then_falls_back():
    transport, _state = _transport(
        [
            _json_response(500, {}),
            _json_response(500, {}),
            _json_response(500, {}),
            _completion("fallback answer", model="fallback/model"),
        ]
    )
    provider = _provider(transport)
    response = provider.complete([{"role": "user", "content": "hi"}])
    assert response.text == "fallback answer"
    assert provider.requests == [
        "primary/model",
        "primary/model",
        "primary/model",
        "fallback/model",
    ]


def test_openrouter_timeout_retries_then_falls_back():
    transport, _state = _transport(
        [
            httpx.TimeoutException("boom"),
            httpx.TimeoutException("boom"),
            httpx.TimeoutException("boom"),
            _completion("fallback after timeout", model="fallback/model"),
        ]
    )
    provider = _provider(transport)
    response = provider.complete([{"role": "user", "content": "hi"}])
    assert response.text == "fallback after timeout"
    assert provider.requests[3] == "fallback/model"


def test_openrouter_malformed_body_exhausts_chain():
    transport, _state = _transport(
        [
            httpx.Response(200, text="<html>not json</html>"),
        ]
    )
    provider = _provider(transport)
    with pytest.raises(ProviderUnavailable):
        provider.complete([{"role": "user", "content": "hi"}])
    assert len(provider.requests) == 6  # 3 per model across 2 models


def test_openrouter_empty_choices_exhausts_chain():
    transport, _state = _transport([_json_response(200, {"choices": []})])
    provider = _provider(transport)
    with pytest.raises(ProviderUnavailable):
        provider.complete([{"role": "user", "content": "hi"}])


def test_openrouter_timeout_exhausts_chain():
    transport, _state = _transport([httpx.TimeoutException("boom")])
    provider = _provider(transport)
    with pytest.raises(ProviderUnavailable):
        provider.complete([{"role": "user", "content": "hi"}])


def test_openrouter_without_key_is_not_configured():
    from apps.ai.providers.base import ProviderNotConfigured

    provider = OpenRouterProvider(api_key="", model="x")
    with pytest.raises(ProviderNotConfigured):
        provider.complete([{"role": "user", "content": "hi"}])


def test_openrouter_parses_tool_calls():
    body = {
        "model": "primary/model",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "check_availability",
                                "arguments": json.dumps({"date": "2026-01-05"}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }
    transport, _state = _transport([_json_response(200, body)])
    provider = _provider(transport)
    response = provider.complete([{"role": "user", "content": "hi"}])
    assert response.tool_calls[0].name == "check_availability"
    assert response.tool_calls[0].arguments == {"date": "2026-01-05"}


# --------------------------------------------------------------------------- #
# Factory and hashing
# --------------------------------------------------------------------------- #


def test_factory_defaults_to_mock(settings):
    settings.AI_PROVIDER = "mock"
    provider = get_provider()
    assert isinstance(provider, MockProvider)


def test_factory_unknown_provider_raises():
    with pytest.raises(ValueError):
        get_provider("nope")


def test_request_and_response_hash_are_stable():
    messages = [{"role": "user", "content": "hello"}]
    assert request_hash(messages, model="m") == request_hash(messages, model="m")
    from apps.ai.providers.base import AIResponse, Usage

    response = AIResponse(text="x", model="m", usage=Usage())
    assert response_hash(response) == response_hash(response)
