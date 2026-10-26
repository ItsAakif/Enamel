"""Sandbox payment gateway (T119).

The sandbox implements the gateway protocol entirely locally: it signs payloads
with ``PAYMENT_WEBHOOK_SECRET`` and returns a hosted-redirect URL that encodes
only a reference and an amount. It never opens a socket (pytest-socket would
block one) and it never sees card data -- the redirect is where a real gateway
would collect it, off this server.

This is the deterministic dev/simulator implementation required by
ARCHITECTURE.md; a live gateway would be added behind the same protocol.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from django.conf import settings

from .base import PaymentIntent

NAME = "sandbox"


def _secret() -> str:
    return str(getattr(settings, "PAYMENT_WEBHOOK_SECRET", "") or "")


class SandboxGateway:
    """A local, network-free gateway with signed redirect semantics."""

    name = NAME

    def create_intent(
        self, *, invoice_number: str, amount_minor: int, currency: str
    ) -> PaymentIntent:
        digest = hashlib.sha256(
            f"{invoice_number}:{amount_minor}:{currency}".encode()
        ).hexdigest()
        gateway_payment_id = f"sbx_{digest[:24]}"
        redirect_url = (
            f"https://sandbox.enamel.local/pay/{gateway_payment_id}"
            f"?amount_minor={amount_minor}&currency={currency}"
        )
        return PaymentIntent(
            gateway_payment_id=gateway_payment_id,
            redirect_url=redirect_url,
            amount_minor=amount_minor,
            currency=currency,
        )

    def refund(
        self, *, gateway_payment_id: str, amount_minor: int, currency: str
    ) -> str:
        digest = hashlib.sha256(
            f"refund:{gateway_payment_id}:{amount_minor}:{currency}".encode()
        ).hexdigest()
        return f"sbxr_{digest[:24]}"

    def sign_payload(self, *, timestamp: str, body: bytes) -> str:
        return hmac.new(
            _secret().encode(), f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest()

    def build_webhook(
        self, payload: dict[str, Any], *, timestamp: str | None = None
    ) -> tuple[bytes, str, str]:
        """Return ``(body, timestamp, signature)`` for a test webhook."""

        moment = timestamp or str(int(time.time()))
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return body, moment, self.sign_payload(timestamp=moment, body=body)

    def verify_webhook(self, *, timestamp: str, body: bytes, signature: str) -> bool:
        from ..services.reconcile import verify_signature

        return verify_signature(
            secret=_secret(), timestamp=timestamp, body=body, signature=signature
        )


__all__ = ["NAME", "SandboxGateway"]
