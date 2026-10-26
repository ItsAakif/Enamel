"""Provider selection (T123).

``get_provider()`` reads ``AI_PROVIDER`` (``mock`` or ``openrouter``) and the
matching settings, returning a ready provider instance. Model ids and the
fallback chain are configuration, never hard-coded in domain code.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from .base import AIProvider
from .mock import MockProvider
from .openrouter import DEFAULT_BASE_URL, OpenRouterProvider

MOCK = "mock"
OPENROUTER = "openrouter"
KNOWN_PROVIDERS = (MOCK, OPENROUTER)


def _setting(name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def get_provider(name: str | None = None, **overrides: Any) -> AIProvider:
    """Return the configured provider.

    ``name`` overrides ``AI_PROVIDER``; ``overrides`` are forwarded to the
    adapter constructor (used by tests to inject faults, transports, or models).
    """

    provider = (name or _setting("AI_PROVIDER", MOCK) or MOCK).strip().lower()
    if provider == MOCK:
        model = overrides.pop("model", None) or _setting("AI_MODEL", "") or "mock-deterministic"
        # The mock must emit vectors matching the configured pgvector column so
        # RAG ingestion works offline without a real embedding provider.
        overrides.setdefault("embedding_dim", int(_setting("AI_EMBEDDING_DIM", 8) or 8))
        return MockProvider(model=model, **overrides)
    if provider == OPENROUTER:
        primary_model = overrides.pop("model", None) or _setting("AI_MODEL", "") or _setting(
            "AI_CHAT_MODEL", ""
        )
        fallback = overrides.pop("fallback_models", None)
        if fallback is None:
            # ``AI_FALLBACK_MODEL`` (the single recommend-a-fallback knob set
            # from the benchmark) comes first, then the comma-separated chain.
            fallback = []
            primary_fallback = str(_setting("AI_FALLBACK_MODEL", "") or "").strip()
            if primary_fallback:
                fallback.append(primary_fallback)
            fallback.extend(str(item) for item in (_setting("AI_FALLBACK_MODELS", []) or []))
            fallback = list(dict.fromkeys(f for f in fallback if f and f != primary_model))
        allow_paid = overrides.pop(
            "allow_paid", bool(_setting("AI_ALLOW_PAID", False))
        )
        return OpenRouterProvider(
            api_key=overrides.pop("api_key", None) or _setting("OPENROUTER_API_KEY", ""),
            base_url=overrides.pop("base_url", None)
            or _setting("OPENROUTER_BASE_URL", DEFAULT_BASE_URL),
            model=primary_model,
            fallback_models=fallback,
            timeout=float(overrides.pop("timeout", None) or _setting("AI_TIMEOUT_SECONDS", 30.0)),
            max_retries=int(overrides.pop("max_retries", None) or _setting("AI_MAX_RETRIES", 2)),
            # The free-model guard is armed unless the operator explicitly opts
            # into paid models: a paid id is refused before any HTTP call.
            free_guard=not allow_paid,
            allow_paid=allow_paid,
            **overrides,
        )
    raise ValueError(
        f"Unknown AI_PROVIDER '{provider}'; expected one of {', '.join(KNOWN_PROVIDERS)}."
    )


__all__ = ["KNOWN_PROVIDERS", "MOCK", "OPENROUTER", "get_provider"]
