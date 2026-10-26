"""Cost computation (T123).

Prices are configuration (``AI_PRICE_TABLE``), expressed in USD per one million
tokens. ``compute_cost_minor`` converts a token count into integer minor units
(cents) with half-up rounding, so cost arithmetic never drifts.

The mock provider is priced at zero, which keeps the test suite and local runs
free while still exercising the arithmetic.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.conf import settings

#: USD per 1M tokens, ``{"prompt": float, "completion": float}``.
DEFAULT_PRICE_TABLE: dict[str, dict[str, float]] = {
    "mock-deterministic": {"prompt": 0.0, "completion": 0.0},
    "openrouter/auto": {"prompt": 0.0, "completion": 0.0},
    "anthropic/claude-3.5-sonnet": {"prompt": 3.0, "completion": 15.0},
    "openai/gpt-4o-mini": {"prompt": 0.15, "completion": 0.6},
    "meta-llama/llama-3.1-8b-instruct:free": {"prompt": 0.0, "completion": 0.0},
}
DEFAULT_PRICE: dict[str, float] = {"prompt": 0.0, "completion": 0.0}


def price_table() -> dict[str, dict[str, float]]:
    return getattr(settings, "AI_PRICE_TABLE", None) or DEFAULT_PRICE_TABLE


def price_for(model: str) -> dict[str, float]:
    return price_table().get(model or "", DEFAULT_PRICE)


def usd_cost(model: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
    price = price_for(model)
    prompt = Decimal(str(price.get("prompt", 0.0))) * Decimal(int(prompt_tokens or 0))
    completion = Decimal(str(price.get("completion", 0.0))) * Decimal(int(completion_tokens or 0))
    return (prompt + completion) / Decimal(1_000_000)


def compute_cost_minor(model: str, prompt_tokens: int, completion_tokens: int) -> int:
    """Return the call cost in minor units (cents), rounded half-up."""

    cents = usd_cost(model, prompt_tokens, completion_tokens) * Decimal(100)
    return int(cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "DEFAULT_PRICE",
    "DEFAULT_PRICE_TABLE",
    "as_float",
    "compute_cost_minor",
    "price_for",
    "price_table",
    "usd_cost",
]
