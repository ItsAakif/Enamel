"""External e-signature adapter (T117).

This is the only place a real e-signature vendor would be integrated, and it is
**disabled by default**: with ``ESIGN_PROVIDER`` unset every call raises
:class:`AdapterNotConfigured`. The application never claims a legally compliant
e-signature unless an operator has explicitly configured a provider *and* the
vendor has actually signed the document; ``drawn``/``typed`` captures stay
labelled as convenience marks only.

Keeping the boundary here means the rest of the domain has no import of any
vendor SDK and no network dependency in tests (pytest-socket would block one
anyway).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from django.conf import settings


class AdapterNotConfigured(RuntimeError):
    """No e-signature provider is configured; the feature is disabled."""


@dataclass(frozen=True)
class EsignRequest:
    """The transient payload handed to an e-sign vendor."""

    document_key: str
    signer_name: str
    signer_email: str
    template_version: int
    callback_url: str = ""


class EsignAdapter(Protocol):
    """The minimal contract an e-sign provider must satisfy."""

    name: str

    def send_for_signature(self, request: EsignRequest) -> str:  # pragma: no cover - protocol
        """Return the vendor envelope id."""

        ...


def configured_provider() -> str:
    """Return the configured ``ESIGN_PROVIDER`` name (empty when disabled)."""

    return str(getattr(settings, "ESIGN_PROVIDER", "") or "").strip().lower()


def get_esign_adapter() -> EsignAdapter:
    """Return the configured adapter, or raise when e-sign is disabled."""

    provider = configured_provider()
    if not provider:
        raise AdapterNotConfigured(
            "ESIGN_PROVIDER is not configured; external e-signature is disabled."
        )
    raise AdapterNotConfigured(f"unknown ESIGN_PROVIDER '{provider}'")


__all__ = [
    "AdapterNotConfigured",
    "EsignAdapter",
    "EsignRequest",
    "configured_provider",
    "get_esign_adapter",
]
