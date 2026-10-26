"""Payment gateway adapter registry (T119).

``PAYMENT_GATEWAY`` selects the transport. The default ``sandbox`` is fully local
and never opens a socket; a live gateway would be registered here behind the same
protocol.
"""

from __future__ import annotations

from django.conf import settings

from .base import AdapterError, AdapterNotConfigured, PaymentGateway, PaymentIntent
from .sandbox_gateway import SandboxGateway

GATEWAY_CHOICES = ("sandbox",)


def configured_gateway() -> str:
    value = str(getattr(settings, "PAYMENT_GATEWAY", "sandbox") or "sandbox")
    return value.strip().lower() or "sandbox"


def get_gateway(name: str | None = None) -> PaymentGateway:
    """Return the gateway adapter for ``name`` (default: the configured one)."""

    selected = (name or configured_gateway()).strip().lower()
    if selected == "sandbox":
        return SandboxGateway()
    raise AdapterNotConfigured(f"unknown PAYMENT_GATEWAY '{selected}'")


__all__ = [
    "GATEWAY_CHOICES",
    "AdapterError",
    "AdapterNotConfigured",
    "PaymentGateway",
    "PaymentIntent",
    "SandboxGateway",
    "configured_gateway",
    "get_gateway",
]
