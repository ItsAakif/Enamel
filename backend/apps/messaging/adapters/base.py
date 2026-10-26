"""Channel adapter contract (T114).

An adapter receives one immutable :class:`OutboundMessage` (the projection of a
``Message`` row plus the resolved raw address and rendered body) and returns a
provider message id. Adapters never touch the database and never decide policy:
the gate order lives in ``services.dispatch``.

Failure modes are explicit:

* :class:`AdapterNotConfigured` - the channel has no usable transport (missing
  credentials, an unapproved WhatsApp template, a live provider not enabled in
  this build). Dispatch records a skip, it does not retry.
* :class:`AdapterError` - a transient provider failure. Dispatch retries with
  backoff and finally marks the message ``failed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AdapterError(RuntimeError):
    """A provider send failed and may be retried."""


class AdapterNotConfigured(AdapterError):
    """The channel has no usable transport; retrying would not help."""


@dataclass(frozen=True)
class OutboundMessage:
    """The transient payload handed to an adapter.

    ``to_address`` is the raw email/phone resolved at send time; it is never
    persisted. ``body``/``subject`` are rendered from the template context.
    """

    id: str
    clinic_id: str
    channel: str
    purpose: str
    to_address: str
    body: str
    subject: str = ""


class MessagingAdapter(Protocol):
    """The minimal contract every channel adapter implements."""

    name: str

    def send(self, message: OutboundMessage) -> str:  # pragma: no cover - protocol
        """Deliver ``message`` and return the provider message id."""

        ...


__all__ = [
    "AdapterError",
    "AdapterNotConfigured",
    "MessagingAdapter",
    "OutboundMessage",
]
