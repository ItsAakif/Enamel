"""Payment gateway adapter contract (T119)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AdapterError(RuntimeError):
    """A gateway call failed and may be retried."""


class AdapterNotConfigured(AdapterError):
    """No usable gateway is configured; retrying would not help."""


@dataclass(frozen=True)
class PaymentIntent:
    """The result of asking a gateway to accept a payment."""

    gateway_payment_id: str
    redirect_url: str
    amount_minor: int
    currency: str


class PaymentGateway(Protocol):
    """The minimal contract every payment gateway implements."""

    name: str

    def create_intent(  # pragma: no cover - protocol
        self, *, invoice_number: str, amount_minor: int, currency: str
    ) -> PaymentIntent:
        ...

    def refund(  # pragma: no cover - protocol
        self, *, gateway_payment_id: str, amount_minor: int, currency: str
    ) -> str:
        ...

    def verify_webhook(  # pragma: no cover - protocol
        self, *, timestamp: str, body: bytes, signature: str
    ) -> bool:
        ...


__all__ = [
    "AdapterError",
    "AdapterNotConfigured",
    "PaymentGateway",
    "PaymentIntent",
]
