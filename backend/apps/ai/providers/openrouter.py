"""OpenRouter provider adapter (T123).

Thin ``httpx`` client for the OpenRouter chat-completions API. The adapter owns
three responsibilities and nothing else:

1. **Mandatory timeout.** No request is ever sent without one; the default comes
   from ``AI_TIMEOUT_SECONDS`` and the caller may shorten it per call.
2. **Retries with jitter.** A 429, 5xx, timeout or malformed body is retried up
   to ``AI_MAX_RETRIES`` times (default 2). A 429 that carries ``Retry-After``
   is honoured exactly (never shortened); otherwise the backoff starts at
   5 s and is capped at 45 s -- free-model pools are shared infrastructure and
   hammering them only produces more 429s.
3. **Fallback chain.** When a model exhausts its retries the next model from
   ``AI_FALLBACK_MODELS`` is tried. When the whole chain is exhausted a typed
   :class:`ProviderUnavailable` is raised -- never a fabricated answer.

The HTTP client is injectable (``transport`` or ``client``) so tests drive the
adapter through an ``httpx.MockTransport`` and never open a socket. Sleeping and
jitter are injectable for the same reason.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx
from django.conf import settings

from .base import (
    AIResponse,
    Message,
    PaidModelBlocked,
    ProviderEmpty,
    ProviderMalformed,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderServerError,
    ProviderTimeout,
    ProviderUnavailable,
    ToolCall,
    TransientProviderError,
    Usage,
    coerce_messages,
)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
#: Fallback backoff start for 429/5xx/timeout retries (seconds).
BACKOFF_BASE_SECONDS = 5.0
#: ...and the exponential cap. Free-model pools are congested shared
#: infrastructure; backing off for minutes is better than hammering.
BACKOFF_MAX_SECONDS = 45.0
BACKOFF_JITTER = 0.2


def pricing_of(entry: dict[str, Any]) -> tuple[float, float]:
    """Extract ``(prompt, completion)`` USD-per-token pricing from a catalogue entry."""

    pricing = entry.get("pricing") if isinstance(entry, dict) else None
    if not isinstance(pricing, dict):
        return (0.0, 0.0)
    return (
        _as_float(pricing.get("prompt")),
        _as_float(pricing.get("completion")),
    )


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class OpenRouterProvider:
    """Chat-completions provider with retries and a fallback model chain."""

    name = "openrouter"

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        model: str = "",
        fallback_models: Sequence[str] | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        http_referer: str = "",
        title: str = "Enamel",
        free_guard: bool = False,
        allow_paid: bool = False,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.api_key = api_key or ""
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or ""
        self.fallback_models = [str(item) for item in (fallback_models or []) if str(item).strip()]
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.http_referer = http_referer
        self.title = title
        #: Armed by the factory (unless ``AI_ALLOW_PAID``): before any call the
        #: configured models are resolved in the live ``/models`` catalogue and
        #: paid ones are refused. Direct construction (tests, the benchmark)
        #: opts in explicitly.
        self.free_guard = bool(free_guard)
        self.allow_paid = bool(allow_paid)
        self._transport = transport
        self._client = client
        self._sleep = sleep or time.sleep
        self._rng = rng or random.Random()
        #: Every model attempted, in order. Used by tests to assert the chain.
        self.requests: list[str] = []
        #: Lazily-fetched live catalogue (``{"data": [...]}``), cached per instance.
        self.catalogue: list[dict[str, Any]] | None = None

    # -- internals --------------------------------------------------------- #

    def client(self) -> httpx.Client:
        if self._client is None:
            kwargs: dict[str, Any] = {"timeout": self.timeout}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._client = httpx.Client(**kwargs)
        return self._client

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": self.title,
        }
        if self.http_referer:
            headers["HTTP-Referer"] = self.http_referer
        return headers

    def _backoff(self, attempt: int, retry_after: float | None = None) -> float:
        """Backoff before the next attempt.

        A ``Retry-After`` request from the server is honoured when present:
        it is never shortened (any jitter is added on top). Otherwise an
        exponential backoff starting at :data:`BACKOFF_BASE_SECONDS` capped
        at :data:`BACKOFF_MAX_SECONDS` gets the regular +/- jitter.
        """

        if retry_after is not None:
            return max(0.0, float(retry_after)) * (1 + abs(self._rng.uniform(0, BACKOFF_JITTER)))
        delay = min(BACKOFF_BASE_SECONDS * (2 ** max(0, attempt)), BACKOFF_MAX_SECONDS)
        spread = delay * BACKOFF_JITTER
        return max(0.0, delay + self._rng.uniform(-spread, spread))

    def _models(self) -> list[str]:
        models = [self.model, *self.fallback_models]
        return [m for m in models if m]

    # -- free-model guard ---------------------------------------------------- #

    def fetch_models(self) -> list[dict[str, Any]]:
        """Fetch ``GET /models`` once and cache the catalogue on the instance.

        Typed provider errors propagate: if the catalogue cannot be read the
        guard cannot vouch for any model and the call is refused rather than
        made unguarded.
        """

        if self.catalogue is not None:
            return self.catalogue
        try:
            response = self.client().get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("The model catalogue fetch timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderServerError("The model catalogue could not be reached.") from exc
        if response.status_code >= 500:
            raise ProviderServerError(
                "The model catalogue returned a server error.",
                status_code=response.status_code,
            )
        if response.status_code != 200:
            raise ProviderUnavailable(
                "The model catalogue rejected the request.",
                status_code=response.status_code,
            )
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ProviderMalformed("The model catalogue returned an invalid body.") from exc
        data = body.get("data") if isinstance(body, dict) else None
        self.catalogue = [item for item in data or [] if isinstance(item, dict)]
        return self.catalogue

    def pricing_for(self, model_id: str) -> tuple[float, float] | None:
        """Return the catalogue pricing for ``model_id`` (``None`` if unresolved)."""

        for entry in self.fetch_models():
            if str(entry.get("id") or "") == model_id:
                return pricing_of(entry)
        return None

    def assert_models_free(self, model_ids: Sequence[str]) -> None:
        """Refuse any configured model whose live catalogue pricing is paid.

        Raises :class:`PaidModelBlocked` before a single HTTP chat request is
        made. A model id that does not resolve in the catalogue is *not*
        blocked here: the upstream API itself rejects unknown ids and the
        failure is surfaced as a typed provider error -- it is never silently
        replaced by another model.
        """

        if not self.free_guard or self.allow_paid:
            return
        checked: set[str] = set()
        for model_id in model_ids:
            if model_id in checked:
                continue
            checked.add(model_id)
            pricing = self.pricing_for(model_id)
            if pricing is None:
                continue
            prompt, completion = pricing
            if prompt > 0 or completion > 0:
                raise PaidModelBlocked(
                    f"Model '{model_id}' has paid pricing "
                    f"(prompt=${prompt:g}/tok, completion=${completion:g}/tok); "
                    "set AI_ALLOW_PAID=1 to allow it.",
                )

    def _parse_tool_calls(self, raw: Any) -> list[ToolCall]:
        calls: list[ToolCall] = []
        if not isinstance(raw, list):
            return calls
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            function = item.get("function") or {}
            name = str(function.get("name") or "")
            if not name:
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    parsed = json.loads(arguments)
                except (TypeError, ValueError):
                    parsed = {"_raw": arguments}
            elif isinstance(arguments, dict):
                parsed = arguments
            else:
                parsed = {}
            calls.append(
                ToolCall(
                    id=str(item.get("id") or f"call_{index}"),
                    name=name,
                    arguments=parsed if isinstance(parsed, dict) else {},
                )
            )
        return calls

    def _call_model(
        self,
        model: str,
        messages: Sequence[Message],
        tools: Sequence[Any] | None,
        response_schema: dict[str, Any] | None,
        timeout: float,
        *,
        response_format: dict[str, Any] | None = None,
        tool_choice: Any = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        seed: int | None = None,
    ) -> AIResponse:
        self.requests.append(model)
        payload: dict[str, Any] = {
            "model": model,
            "messages": [message.as_dict() for message in messages],
        }
        if tools:
            payload["tools"] = list(tools)
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        # ``response_format`` (json_object / json_schema) wins when given
        # explicitly; a bare JSON schema keeps the historical json_object body.
        if response_format is not None:
            payload["response_format"] = response_format
        elif response_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        if temperature is not None:
            payload["temperature"] = float(temperature)
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)
        if seed is not None:
            payload["seed"] = int(seed)

        started = time.perf_counter()
        try:
            response = self.client().post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("The provider timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderServerError("The provider could not be reached.") from exc

        if response.status_code == 429:
            retry_after = None
            raw_retry_after = response.headers.get("Retry-After")
            if raw_retry_after:
                try:
                    retry_after = float(raw_retry_after)
                except (TypeError, ValueError):
                    retry_after = None
            raise ProviderRateLimited(
                "The provider rate limited the request.",
                status_code=429,
                retry_after=retry_after,
            )
        if response.status_code >= 500:
            raise ProviderServerError(
                "The provider returned a server error.",
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise ProviderUnavailable(
                "The provider rejected the request.",
                status_code=response.status_code,
            )

        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ProviderMalformed("The provider returned an invalid body.") from exc
        if not isinstance(body, dict):
            raise ProviderMalformed("The provider returned an invalid body.")

        choices = body.get("choices")
        if not choices:
            raise ProviderEmpty("The provider returned no choices.")
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") or {}
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        tool_calls = self._parse_tool_calls(message.get("tool_calls"))
        if not text and not tool_calls:
            raise ProviderEmpty("The provider returned an empty completion.")

        usage = body.get("usage") or {}
        cost_raw = usage.get("cost") if isinstance(usage, dict) else None
        return AIResponse(
            text=text,
            # The *actual* model the API routed to, not the requested id.
            model=str(body.get("model") or model),
            usage=Usage(
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                cost_usd=_as_float(cost_raw) if cost_raw is not None else None,
            ),
            tool_calls=tool_calls,
            finish_reason=str(first.get("finish_reason") or "stop"),
            provider=self.name,
            native_provider=str(body.get("provider") or ""),
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=1,
        )

    # -- AIProvider -------------------------------------------------------- #

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
    ) -> AIResponse:
        if not self.api_key:
            raise ProviderNotConfigured("OPENROUTER_API_KEY is not configured.")
        models = self._models()
        if not models:
            raise ProviderNotConfigured("No AI model is configured.")
        # The guard runs before the first HTTP chat request; a refusal is a
        # policy error, not a retry/fallback signal.
        self.assert_models_free(models)
        request_timeout = float(timeout) if timeout is not None else self.timeout
        msgs = coerce_messages(messages)

        last_error: Exception | None = None
        attempts = 0
        for model in models:
            attempt = 0
            while True:
                attempts += 1
                try:
                    response = self._call_model(
                        model,
                        msgs,
                        tools,
                        response_schema,
                        request_timeout,
                        response_format=response_format,
                        tool_choice=tool_choice,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        seed=seed,
                    )
                    response.attempts = attempts
                    return response
                except TransientProviderError as exc:
                    last_error = exc
                    if attempt < self.max_retries:
                        self._sleep(
                            self._backoff(attempt, getattr(exc, "retry_after", None))
                        )
                        attempt += 1
                        continue
                    break
        raise ProviderUnavailable(
            "All configured AI models are unavailable.",
            status_code=503,
        ) from last_error

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.api_key:
            raise ProviderNotConfigured("OPENROUTER_API_KEY is not configured.")
        model = getattr(settings, "AI_EMBEDDING_MODEL", "") or self.model
        if not model:
            raise ProviderNotConfigured("No embedding model is configured.")
        payload = {"model": model, "input": list(texts)}
        try:
            response = self.client().post(
                f"{self.base_url}/embeddings",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("The embedding provider is unavailable.") from exc
        if response.status_code >= 400:
            raise ProviderUnavailable("The embedding provider rejected the request.")
        body = response.json()
        data = body.get("data") or []
        vectors: list[list[float]] = []
        for item in data:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            vectors.append([float(value) for value in embedding] if embedding else [])
        return vectors

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "configured": bool(self.api_key),
            "model": self.model,
            "fallback_models": list(self.fallback_models),
            "base_url": self.base_url,
            "free_guard": bool(self.free_guard),
            "allow_paid": bool(self.allow_paid),
        }


__all__ = ["DEFAULT_BASE_URL", "OpenRouterProvider", "pricing_of"]
