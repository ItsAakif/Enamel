"""Isolated external e-signature adapter boundary (T117)."""

from __future__ import annotations

from .esign import (
    AdapterNotConfigured,
    EsignAdapter,
    EsignRequest,
    configured_provider,
    get_esign_adapter,
)

__all__ = [
    "AdapterNotConfigured",
    "EsignAdapter",
    "EsignRequest",
    "configured_provider",
    "get_esign_adapter",
]
