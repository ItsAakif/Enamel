"""Portal adapters (T121)."""

from __future__ import annotations

from .otp import (
    OUTBOX,
    DevOtpAdapter,
    OtpAdapter,
    echo_allowed,
    get_adapter,
    reset_outbox,
)

__all__ = [
    "OUTBOX",
    "DevOtpAdapter",
    "OtpAdapter",
    "echo_allowed",
    "get_adapter",
    "reset_outbox",
]
