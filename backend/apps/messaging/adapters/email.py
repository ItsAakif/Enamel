"""Email adapter backed by Django's email backend (T114).

In development and tests ``EMAIL_BACKEND`` is the console or locmem backend, so
no socket is opened. ``send_mail`` raises on a real transport failure, which
dispatch turns into a retryable ``AdapterError``.
"""

from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail

from .base import AdapterError, AdapterNotConfigured, OutboundMessage


class EmailAdapter:
    name = "smtp"

    def send(self, message: OutboundMessage) -> str:
        if not message.to_address:
            raise AdapterNotConfigured("No email address is available for this message.")
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@enamel.local")
        try:
            sent = send_mail(
                message.subject or "Enamel notification",
                message.body,
                from_email,
                [message.to_address],
                fail_silently=False,
            )
        except Exception as exc:  # noqa: BLE001 - transport failures are retryable
            raise AdapterError(f"email transport failed: {exc}") from exc
        if not sent:
            raise AdapterError("email transport accepted zero messages")
        return f"email-{message.id}"


__all__ = ["EmailAdapter"]
