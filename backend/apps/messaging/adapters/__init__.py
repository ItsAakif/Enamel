"""Channel adapter registry (T114).

``MESSAGING_ADAPTER`` selects the configured transport; ``resolve_adapter`` maps
a message channel onto the adapter that can serve it. The default (``dev``)
serves every channel deterministically and never opens a socket.
"""

from __future__ import annotations

from django.conf import settings

from .base import AdapterError, AdapterNotConfigured, MessagingAdapter, OutboundMessage
from .dev import DevAdapter
from .email import EmailAdapter
from .sms import SmsAdapter
from .whatsapp import WhatsAppAdapter

ADAPTER_CHOICES = ("dev", "smtp", "twilio", "whatsapp_cloud")


def configured_adapter() -> str:
    value = str(getattr(settings, "MESSAGING_ADAPTER", "dev") or "dev").strip().lower()
    return value or "dev"


def resolve_adapter(channel: str) -> MessagingAdapter:
    """Return the adapter for ``channel`` under the configured transport."""

    configured = configured_adapter()
    if configured == "dev":
        return DevAdapter()
    if configured == "smtp":
        if channel == "email":
            return EmailAdapter()
        raise AdapterNotConfigured(
            f"channel '{channel}' is not configured for adapter 'smtp'"
        )
    if configured == "twilio":
        if channel == "sms":
            return SmsAdapter()
        if channel == "email":
            return EmailAdapter()
        raise AdapterNotConfigured(
            f"channel '{channel}' is not configured for adapter 'twilio'"
        )
    if configured == "whatsapp_cloud":
        if channel == "whatsapp":
            return WhatsAppAdapter()
        if channel == "email":
            return EmailAdapter()
        if channel == "sms":
            return SmsAdapter()
        raise AdapterNotConfigured(
            f"channel '{channel}' is not configured for adapter 'whatsapp_cloud'"
        )
    raise AdapterNotConfigured(f"unknown MESSAGING_ADAPTER '{configured}'")


__all__ = [
    "ADAPTER_CHOICES",
    "AdapterError",
    "AdapterNotConfigured",
    "MessagingAdapter",
    "OutboundMessage",
    "configured_adapter",
    "resolve_adapter",
]
