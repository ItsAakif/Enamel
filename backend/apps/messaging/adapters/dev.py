"""Deterministic development adapter (T114).

Never performs network I/O. The returned id is derived from the message id, so
re-sending the same row produces the same provider id and tests can assert it
exactly.
"""

from __future__ import annotations

from .base import OutboundMessage


class DevAdapter:
    """Records a send without leaving the process."""

    name = "dev"

    def send(self, message: OutboundMessage) -> str:
        return f"dev-{message.id}"


__all__ = ["DevAdapter"]
