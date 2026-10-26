"""Development OTP delivery adapter (T121).

The dev adapter never opens a socket. It writes the code to the
``enamel.portal.otp`` logger and appends ``{channel, contact, code}`` to a
module-level :data:`OUTBOX` so the test suite can read the code without a mail
or SMS provider.

``PORTAL_OTP_ECHO=true`` additionally allows the API to echo the code in the
response -- but only when ``DEBUG`` is also true, and only because production
settings never enable either. This is the single place that decides whether a
code may be echoed; no view reads the flag directly.

``PORTAL_OTP_LOG_PATH`` optionally names a file the code is appended to (one
JSON object per line). It is off by default: an operator has to ask for it
explicitly. The compose rehearsal stack sets it so the end-to-end harness can
read the code of a synthetic patient when the API does not echo it
(``DEBUG`` is false in the production profile). The file holds contact
identifiers and one-time codes, so it has to be treated as sensitive and it
never belongs in the log stream.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from django.conf import settings

logger = logging.getLogger("enamel.portal.otp")

#: Test-facing outbox. Cleared by the portal test fixture between tests.
OUTBOX: list[dict[str, Any]] = []


class OtpAdapter(Protocol):
    """Deliver a one-time code over one channel."""

    def send(self, *, channel: str, contact: str, code: str) -> None: ...


def _append_to_file(channel: str, contact: str, code: str) -> None:
    """Append the code to the configured file. Never raises."""

    path = str(getattr(settings, "PORTAL_OTP_LOG_PATH", "") or "").strip()
    if not path:
        return
    try:
        target = Path(path)
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "channel": channel,
                "contact": contact,
                "code": code,
            },
            ensure_ascii=True,
        )
        with target.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")
    except OSError:
        # The code is still in the log record and the outbox; a missing file
        # must never break the request that asked for a code.
        logger.warning("portal_otp_log_write_failed")


class DevOtpAdapter:
    """Log/outbox adapter used everywhere except production."""

    name = "dev"

    def send(self, *, channel: str, contact: str, code: str) -> None:
        logger.info(
            "portal_otp_code",
            extra={
                "channel": channel,
                "contact_masked": _mask(contact),
                "portal_otp_code": code,
            },
        )
        OUTBOX.append({"channel": channel, "contact": contact, "code": code})
        _append_to_file(channel, contact, code)


def get_adapter() -> OtpAdapter:
    return DevOtpAdapter()


def echo_allowed() -> bool:
    """True only in DEBUG with ``PORTAL_OTP_ECHO`` explicitly set."""

    return bool(getattr(settings, "DEBUG", False)) and bool(
        getattr(settings, "PORTAL_OTP_ECHO", False)
    )


def reset_outbox() -> None:
    OUTBOX.clear()


def _mask(value: str) -> str:
    text = str(value or "")
    if len(text) <= 2:
        return "*" * len(text)
    return text[0] + "*" * (len(text) - 2) + text[-1]


__all__ = [
    "OUTBOX",
    "DevOtpAdapter",
    "OtpAdapter",
    "echo_allowed",
    "get_adapter",
    "reset_outbox",
]
