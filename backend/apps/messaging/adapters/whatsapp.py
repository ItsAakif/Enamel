"""WhatsApp Cloud adapter (T114).

WhatsApp business messaging only allows *utility* category templates. This build
ships no approved WhatsApp template and no live transport, so the adapter raises
:class:`AdapterNotConfigured` unless the Cloud credentials are present, and
never opens a socket.
"""

from __future__ import annotations

from django.conf import settings

from .base import AdapterNotConfigured, OutboundMessage


class WhatsAppAdapter:
    name = "whatsapp_cloud"

    def send(self, message: OutboundMessage) -> str:
        phone_number_id = getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "")
        access_token = getattr(settings, "WHATSAPP_ACCESS_TOKEN", "")
        if not phone_number_id or not access_token:
            raise AdapterNotConfigured("WhatsApp Cloud credentials are not configured.")
        raise AdapterNotConfigured(
            "WhatsApp Cloud utility-template delivery is not available in this build."
        )


__all__ = ["WhatsAppAdapter"]
