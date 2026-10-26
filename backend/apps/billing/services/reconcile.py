"""Gateway webhook processing and reconciliation (T119).

Inbound payment webhooks are authenticated with an HMAC-SHA256 signature over
``"{timestamp}.{body}"`` using ``PAYMENT_WEBHOOK_SECRET``. A signature that does
not verify, or a timestamp outside ``PAYMENT_WEBHOOK_TOLERANCE_SECONDS``, is
rejected before anything is stored.

Processing is idempotent: ``(gateway, event_id)`` is unique, so a replayed event
is recorded once and never credits an invoice twice. An event that cannot be
matched to a known payment (or carries no payment reference) is not dropped --
it lands in the reconciliation queue with a reason so an operator can resolve
it.
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
from apps.core.rls import set_rls_context
from apps.core.tenancy import with_clinic

from ..models import Payment, ReconciliationItem, WebhookEvent
from . import payments as payment_service
from . import refunds as refund_service

SUCCEEDED_EVENTS = {"payment.succeeded", "payment.captured", "succeeded"}
FAILED_EVENTS = {"payment.failed", "failed"}
REFUNDED_EVENTS = {"payment.refunded", "refunded"}


def tolerance_seconds() -> int:
    return int(getattr(settings, "PAYMENT_WEBHOOK_TOLERANCE_SECONDS", 300))


def compute_signature(secret: str, timestamp: str, body: bytes) -> str:
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_signature(
    *,
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    now: datetime | None = None,
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


def _event_type(payload: dict[str, Any]) -> str:
    return str(payload.get("type") or payload.get("event_type") or "").strip().lower()


def _payment_reference(payload: dict[str, Any]) -> str:
    return str(
        payload.get("gateway_payment_id")
        or payload.get("payment_id")
        or payload.get("reference")
        or ""
    ).strip()


def _amount_minor(payload: dict[str, Any]) -> int:
    value = payload.get("amount_minor")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _unmatched(event: WebhookEvent, reason: str, amount_minor: int) -> dict[str, Any]:
    event.status = WebhookEvent.Status.UNMATCHED
    event.processed_at = timezone.now()
    event.save(update_fields=["status", "processed_at"])
    ReconciliationItem.objects.create(
        clinic=event.clinic,
        event=event,
        reason=reason,
        amount_minor=amount_minor,
        status=ReconciliationItem.Status.OPEN,
    )
    return {"status": "unmatched", "event_id": event.event_id, "reason": reason}


def process_event(
    gateway: str,
    payload: dict[str, Any],
    *,
    signature_valid: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply one gateway event; always returns a small status dict."""

    event_id = str(payload.get("event_id") or "").strip()
    if not event_id:
        return {"status": "ignored", "reason": "missing_event_id"}

    reference = _payment_reference(payload)
    event_type = _event_type(payload)
    amount_minor = _amount_minor(payload)
    moment = now or timezone.now()

    # The signed event is the capability of this request (T137): the three
    # GUCs are what makes the payment row and the event row reachable without
    # a clinic context.
    set_rls_context(
        webhook_gateway=gateway,
        webhook_event_id=event_id,
        webhook_reference=reference,
    )

    with transaction.atomic():
        try:
            with transaction.atomic():
                event = WebhookEvent.objects.create(
                    gateway=gateway,
                    event_id=event_id,
                    payload_json={
                        "event_id": event_id,
                        "type": event_type,
                        "gateway_payment_id": reference,
                        "amount_minor": amount_minor,
                    },
                    signature_valid=signature_valid,
                    status=WebhookEvent.Status.RECEIVED,
                    received_at=moment,
                )
        except IntegrityError:
            # A replayed event: the unique constraint is the replay guard.
            return {"status": "duplicate", "event_id": event_id}

        payment = None
        if reference:
            payment = Payment.all_objects.filter(
                gateway=gateway, gateway_payment_id=reference
            ).first()

        if payment is None:
            reason = "missing_payment_reference" if not reference else "unmatched_payment"
            result = _unmatched(event, reason, amount_minor)
            record_audit(
                "payment.webhook_unmatched",
                entity=event,
                after={"gateway": gateway, "event_id": event_id, "reason": reason},
            )
            return result

        # The clinic is known now: every write below (the event, the payment,
        # the invoice, the reconciliation item and the audit event) happens
        # inside that clinic's context.
        with with_clinic(payment.clinic):
            event.clinic = payment.clinic
            if event_type in SUCCEEDED_EVENTS:
                payment_service.credit_pending_payment(payment, now=moment)
            elif event_type in FAILED_EVENTS:
                payment_service.mark_failed(payment)
            elif event_type in REFUNDED_EVENTS:
                remaining = payment.amount_minor - refund_service.refunded_total(payment)
                if remaining > 0:
                    refund_service.refund_payment(
                        payment, remaining, reason="gateway webhook", now=moment
                    )

            if amount_minor and amount_minor != payment.amount_minor:
                # The event was matched but the amount disagrees: flag it for an
                # operator without silently adjusting the books.
                ReconciliationItem.objects.create(
                    clinic=payment.clinic,
                    event=event,
                    reason="amount_mismatch",
                    amount_minor=amount_minor,
                    status=ReconciliationItem.Status.OPEN,
                )

            event.status = WebhookEvent.Status.PROCESSED
            event.processed_at = moment
            event.save(update_fields=["clinic", "status", "processed_at"])
            record_audit(
                "payment.webhook_processed",
                entity=event,
                after={"gateway": gateway, "event_id": event_id, "type": event_type},
            )
    return {
        "status": "processed",
        "event_id": event_id,
        "payment_status": payment.status,
    }


__all__ = [
    "FAILED_EVENTS",
    "REFUNDED_EVENTS",
    "SUCCEEDED_EVENTS",
    "compute_signature",
    "process_event",
    "tolerance_seconds",
    "verify_signature",
]
