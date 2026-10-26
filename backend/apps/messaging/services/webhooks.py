"""Provider webhook handling (T114).

Inbound provider events are authenticated with an HMAC-SHA256 signature over
``"{timestamp}.{body}"`` using ``MESSAGING_WEBHOOK_SECRET``. A timestamp outside
``MESSAGING_WEBHOOK_TOLERANCE_SECONDS`` is rejected (replay protection). The
handler is idempotent: the ``(clinic, event_id)`` unique constraint means a
replayed event is recorded once, and an unknown ``provider_message_id`` is
ignored safely.

STOP/HELP/bounce/complaint events record an opt-out (see
``services.suppression`` for the exact blocking rule).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.audit import record as record_audit
from apps.core.tenancy import with_clinic

from ..models import Message, ProviderEvent, Suppression
from . import suppression

OPT_OUT_EVENTS: dict[str, str] = {
    "stop": Suppression.Reason.STOP,
    "help": Suppression.Reason.HELP,
    "bounce": Suppression.Reason.BOUNCE,
    "complaint": Suppression.Reason.COMPLAINT,
}
PROVIDER_STATUS_MAP = {
    "sent": Message.Status.SENT,
    "delivered": Message.Status.DELIVERED,
    "failed": Message.Status.FAILED,
}
#: Only these forward transitions are applied; anything else is a no-op.
FORWARD = {
    Message.Status.QUEUED: {Message.Status.SENT, Message.Status.DELIVERED, Message.Status.FAILED},
    Message.Status.SENT: {Message.Status.DELIVERED, Message.Status.FAILED},
    Message.Status.DELIVERED: set(),
    Message.Status.FAILED: set(),
    Message.Status.DEAD: set(),
    Message.Status.SKIPPED: set(),
}


def tolerance_seconds() -> int:
    return int(getattr(settings, "MESSAGING_WEBHOOK_TOLERANCE_SECONDS", 300))


def compute_signature(secret: str, timestamp: str, body: bytes) -> str:
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_signature(
    *, secret: str, timestamp: str, body: bytes, signature: str, now: datetime | None = None
) -> bool:
    """True when ``signature`` is valid and ``timestamp`` is inside the window."""

    if not secret or not timestamp or not signature:
        return False
    try:
        sent_at = int(float(timestamp))
    except (TypeError, ValueError):
        return False
    moment = now or timezone.now()
    if abs(int(moment.timestamp()) - sent_at) > tolerance_seconds():
        return False
    expected = compute_signature(secret, timestamp, body)
    return hmac.compare_digest(expected, signature)


def _apply_status(message: Message, target: str, error_code: str) -> bool:
    """Apply a forward status transition; return True when the row changed."""

    if target not in FORWARD.get(message.status, set()):
        return False
    update_fields = ["status", "updated_at"]
    message.status = target
    if target == Message.Status.DELIVERED:
        message.delivered_at = timezone.now()
        update_fields.append("delivered_at")
    if target == Message.Status.FAILED:
        message.error_code = error_code or "provider_failed"
        update_fields.append("error_code")
    message.save(update_fields=update_fields)
    return True


def process_event(adapter: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply one provider event; always returns a small status dict."""

    event_id = str(payload.get("event_id") or "").strip()
    provider_message_id = str(payload.get("provider_message_id") or "").strip()
    if not event_id or not provider_message_id:
        return {"status": "ignored", "reason": "missing_ids"}

    message = Message.all_objects.filter(provider_message_id=provider_message_id).first()
    if message is None:
        return {"status": "ignored", "reason": "unknown_message"}

    event_type = str(payload.get("event_type") or "delivery").strip().lower()
    status_value = str(payload.get("status") or "").strip().lower()
    error_code = str(payload.get("error_code") or "").strip()

    with transaction.atomic():
        with with_clinic(message.clinic):
            try:
                with transaction.atomic():
                    ProviderEvent.all_objects.create(
                        clinic=message.clinic,
                        message=message,
                        event_id=event_id,
                        event_type=event_type,
                        payload_redacted={
                            "adapter": adapter,
                            "event_type": event_type,
                            "status": status_value,
                        },
                        occurred_at=timezone.now(),
                    )
            except IntegrityError:
                return {"status": "duplicate", "event_id": event_id}

            if event_type in OPT_OUT_EVENTS:
                suppression.record_opt_out(
                    message.patient,
                    channel=message.channel,
                    reason=OPT_OUT_EVENTS[event_type],
                )

            changed = False
            if event_type in ("bounce", "complaint"):
                changed = _apply_status(message, Message.Status.FAILED, event_type)
            elif status_value in PROVIDER_STATUS_MAP:
                changed = _apply_status(
                    message, PROVIDER_STATUS_MAP[status_value], error_code
                )

            record_audit(
                "messaging.provider_event",
                entity=message,
                after={
                    "adapter": adapter,
                    "event_id": event_id,
                    "event_type": event_type,
                    "status": message.status,
                    "changed": changed,
                },
            )
    return {
        "status": "processed",
        "event_id": event_id,
        "message_status": message.status,
        "changed": changed,
    }


__all__ = [
    "FORWARD",
    "OPT_OUT_EVENTS",
    "PROVIDER_STATUS_MAP",
    "compute_signature",
    "process_event",
    "tolerance_seconds",
    "verify_signature",
]
