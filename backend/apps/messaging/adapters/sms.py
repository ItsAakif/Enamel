"""SMS adapter (T114).

No live provider is enabled in this build. The adapter is *not configured*
unless ``TWILIO_ACCOUNT_SID`` and ``TWILIO_AUTH_TOKEN`` are present; even then
the transport is deliberately unavailable, so a real provider can never be
reached from tests or development.
"""

from __future__ import annotations

from django.conf import settings

from .base import AdapterNotConfigured, OutboundMessage


class SmsAdapter:
    name = "twilio"

    def send(self, message: OutboundMessage) -> str:
        sid = getattr(settings, "TWILIO_ACCOUNT_SID", "")
        token = getattr(settings, "TWILIO_AUTH_TOKEN", "")
        if not sid or not token:
            raise AdapterNotConfigured("Twilio credentials are not configured.")
        raise AdapterNotConfigured(
            "The Twilio SMS transport is not available in this build."
        )


__all__ = ["SmsAdapter"]
