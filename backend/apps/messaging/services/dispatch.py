"""Gated, durable message dispatch (T114).

Gate order (ARCHITECTURE.md section 12, plus the patient-state pre-check):

1. **Patient state** - a deceased/inactive/archived/soft-deleted patient is
   skipped (``patient_inactive``).
2. **Suppression** - a suppressed address is skipped for every non-transactional
   purpose (``suppressed``).
3. **Consent** - a missing granted consent is skipped (``no_consent``);
   ``transactional`` messages do not require consent.
4. **Quiet hours** - a send inside the clinic-local window is *deferred*: the row
   is queued with ``reason=quiet_hours`` and a durable ``JobRequest`` fires at the
   next window. It is never dropped.
5. **Frequency cap** - the (cap+1)-th message in the rolling period is skipped
   (``capped``).
6. **Render** - the template allowlist is enforced; an unknown variable raises
   :class:`~apps.messaging.services.templates.TemplateError`.
7. **Adapter** - the provider is called; a transport failure retries with
   exponential backoff and finally marks the row ``failed``.

Every skip writes a ``Message`` row with its ``reason`` and never calls an
adapter. ``send`` is idempotent by ``idempotency_key`` (unique per clinic), so a
dispatcher crash and retry produce exactly one row and one send.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.audit import record as record_audit
from apps.core.jobs import durable_task, enqueue
from apps.core.tenancy import with_clinic
from apps.patients.models import Patient

from ..adapters import AdapterError, AdapterNotConfigured, OutboundMessage, resolve_adapter
from ..models import Consent, Message, MessageTemplate
from . import consent as consent_service
from . import policy, suppression, templates
from .templates import TemplateError

MESSAGE_DELIVER_JOB = "messaging.deliver"
MESSAGE_QUEUE = "reminders"

#: Every skip reason written to ``Message.reason``.
SKIP_REASONS = (
    "patient_inactive",
    "suppressed",
    "no_consent",
    "capped",
    "template_unavailable",
    "template_error",
    "adapter_not_configured",
    "consent_revoked",
)


def _snapshot(message: Message, *, reason: str | None = None) -> dict[str, Any]:
    data = {
        "channel": message.channel,
        "purpose": message.purpose,
        "status": message.status,
        "template_key": message.template.key if message.template_id else None,
        "template_version": message.template_version,
        "attempts": message.attempts,
    }
    if reason is not None:
        data["reason"] = reason
    return data


def patient_skip_reason(patient: Any) -> str | None:
    """Return a skip reason when the patient must not be messaged at all."""

    if getattr(patient, "deleted_at", None) is not None:
        return "patient_inactive"
    status = getattr(patient, "status", Patient.Status.ACTIVE)
    if status != Patient.Status.ACTIVE:
        return "patient_inactive"
    return None


def _create_message(
    *,
    clinic: Any,
    patient: Any,
    channel: str,
    purpose: str,
    template: MessageTemplate | None,
    address_hash: str,
    status: str,
    reason: str,
    idempotency_key: str,
    context_json: dict[str, Any] | None = None,
    scheduled_for: datetime | None = None,
) -> Message:
    return Message.all_objects.create(
        clinic=clinic,
        patient=patient,
        channel=channel,
        purpose=purpose,
        template=template,
        template_version=template.version if template is not None else 0,
        to_address_hash=address_hash,
        status=status,
        reason=reason,
        idempotency_key=idempotency_key,
        context_json=context_json or {},
        scheduled_for=scheduled_for,
    )


def _record_skip(
    *,
    clinic: Any,
    patient: Any,
    channel: str,
    purpose: str,
    template: MessageTemplate | None,
    address_hash: str,
    reason: str,
    idempotency_key: str,
    context_json: dict[str, Any] | None = None,
    actor: Any = None,
    request: Any = None,
) -> Message:
    message = _create_message(
        clinic=clinic,
        patient=patient,
        channel=channel,
        purpose=purpose,
        template=template,
        address_hash=address_hash,
        status=Message.Status.SKIPPED,
        reason=reason,
        idempotency_key=idempotency_key,
        context_json=context_json,
    )
    record_audit(
        "messaging.message_skipped",
        entity=message,
        after=_snapshot(message, reason=reason),
        actor=actor,
        request=request,
    )
    return message


def _mark_skip(message: Message, reason: str, *, error_code: str = "") -> Message:
    message.status = Message.Status.SKIPPED
    message.reason = reason
    if error_code:
        message.error_code = error_code
    message.save(update_fields=["status", "reason", "error_code", "updated_at"])
    record_audit(
        "messaging.message_skipped",
        entity=message,
        after=_snapshot(message, reason=reason),
    )
    return message


def send(
    patient: Any,
    channel: str,
    purpose: str,
    template_key: str,
    context: dict[str, Any] | None = None,
    idempotency_key: str = "",
    *,
    now: datetime | None = None,
    defer: bool = True,
    actor: Any = None,
    request: Any = None,
    adapter: Any = None,
) -> Message:
    """Run the gate order for one send and return the resulting ``Message``.

    Idempotent by ``idempotency_key``: a repeat call returns the existing row
    without creating a second message or calling the adapter again.
    """

    clinic = patient.clinic
    moment = now or timezone.now()
    values = dict(context or {})
    key = (idempotency_key or "").strip()

    with transaction.atomic():
        with with_clinic(clinic):
            if key:
                existing = Message.all_objects.filter(
                    clinic=clinic, idempotency_key=key
                ).first()
                if existing is not None:
                    return existing

            address = suppression.address_for(patient, channel)
            digest = suppression.address_hash(channel, address)

            # 1 + 2 + 3: patient state, suppression, consent.
            reason = patient_skip_reason(patient)
            if reason is None and suppression.purpose_is_blocked(purpose):
                if suppression.is_suppressed(clinic, channel, digest):
                    reason = "suppressed"
            if reason is None and purpose != Consent.Purpose.TRANSACTIONAL:
                if not consent_service.is_granted(patient, channel, purpose):
                    reason = "no_consent"
            if reason is not None:
                return _record_skip(
                    clinic=clinic,
                    patient=patient,
                    channel=channel,
                    purpose=purpose,
                    template=None,
                    address_hash=digest,
                    reason=reason,
                    idempotency_key=key,
                    actor=actor,
                    request=request,
                )

            template = templates.get_template(clinic, template_key)
            if (
                template is None
                or not template.is_active
                or template.approval_status != MessageTemplate.ApprovalStatus.APPROVED
            ):
                return _record_skip(
                    clinic=clinic,
                    patient=patient,
                    channel=channel,
                    purpose=purpose,
                    template=template,
                    address_hash=digest,
                    reason="template_unavailable",
                    idempotency_key=key,
                    actor=actor,
                    request=request,
                )

            allowed = [str(name) for name in (template.variables or [])]
            context_snapshot = {name: values[name] for name in allowed if name in values}

            # 4: quiet hours -> defer, never drop.
            if defer and policy.in_quiet_hours(clinic, moment):
                next_at = policy.next_window_start(clinic, moment)
                message = _create_message(
                    clinic=clinic,
                    patient=patient,
                    channel=channel,
                    purpose=purpose,
                    template=template,
                    address_hash=digest,
                    status=Message.Status.QUEUED,
                    reason="quiet_hours",
                    idempotency_key=key,
                    context_json=context_snapshot,
                    scheduled_for=next_at,
                )
                enqueue(
                    MESSAGE_DELIVER_JOB,
                    {"message_id": str(message.id)},
                    due_at=next_at,
                    idempotency_key=f"message:{message.id}",
                    clinic=clinic,
                )
                record_audit(
                    "messaging.message_deferred",
                    entity=message,
                    after={**_snapshot(message), "scheduled_for": next_at.isoformat()},
                    actor=actor,
                    request=request,
                )
                return message

            # 5: frequency cap.
            cap, _period = policy.frequency_cap(clinic)
            if cap > 0 and policy.recent_send_count(patient, channel, now=moment) >= cap:
                return _record_skip(
                    clinic=clinic,
                    patient=patient,
                    channel=channel,
                    purpose=purpose,
                    template=template,
                    address_hash=digest,
                    reason="capped",
                    idempotency_key=key,
                    context_json=context_snapshot,
                    actor=actor,
                    request=request,
                )

            # 6: render (raises TemplateError on an unknown/missing variable).
            rendered = templates.render(template, context_snapshot)

            # 7: adapter.
            message = _create_message(
                clinic=clinic,
                patient=patient,
                channel=channel,
                purpose=purpose,
                template=template,
                address_hash=digest,
                status=Message.Status.QUEUED,
                reason="",
                idempotency_key=key,
                context_json=context_snapshot,
            )
            return deliver(
                message, adapter=adapter, now=moment, rendered=rendered, address=address
            )


def deliver(
    message: Message,
    *,
    adapter: Any = None,
    now: datetime | None = None,
    rendered: templates.Rendered | None = None,
    address: str | None = None,
) -> Message:
    """Render (if needed) and hand one queued message to its adapter."""

    clinic = message.clinic
    moment = now or timezone.now()

    with transaction.atomic():
        with with_clinic(clinic):
            locked = (
                Message.all_objects.select_for_update().filter(pk=message.pk).first()
            )
            if locked is None or locked.status != Message.Status.QUEUED:
                return locked if locked is not None else message

            if rendered is None:
                template = (
                    MessageTemplate.all_objects.filter(pk=locked.template_id).first()
                    if locked.template_id
                    else None
                )
                if template is None:
                    return _mark_skip(locked, "template_unavailable")
                try:
                    rendered = templates.render(template, dict(locked.context_json or {}))
                except TemplateError:
                    return _mark_skip(locked, "template_error")
            if address is None:
                address = suppression.address_for(locked.patient, locked.channel)

            chosen = adapter if adapter is not None else resolve_adapter(locked.channel)
            outbound = OutboundMessage(
                id=str(locked.id),
                clinic_id=str(locked.clinic_id),
                channel=locked.channel,
                purpose=locked.purpose,
                to_address=address,
                body=rendered.body,
                subject=rendered.subject,
            )

            locked.attempts = locked.attempts + 1
            try:
                provider_id = chosen.send(outbound)
            except AdapterNotConfigured:
                locked.error_code = "adapter_not_configured"
                locked.status = Message.Status.SKIPPED
                locked.reason = "adapter_not_configured"
                locked.save(
                    update_fields=[
                        "attempts",
                        "error_code",
                        "status",
                        "reason",
                        "updated_at",
                    ]
                )
                record_audit(
                    "messaging.message_skipped",
                    entity=locked,
                    after=_snapshot(locked, reason="adapter_not_configured"),
                )
                return locked
            except AdapterError as exc:
                locked.error_code = "adapter_error"
                if locked.attempts >= policy.max_attempts():
                    locked.status = Message.Status.FAILED
                    locked.save(
                        update_fields=["attempts", "error_code", "status", "updated_at"]
                    )
                    record_audit(
                        "messaging.message_failed",
                        entity=locked,
                        after={
                            **_snapshot(locked),
                            "error_code": "adapter_error",
                            "error": str(exc)[:200],
                        },
                    )
                    return locked
                locked.status = Message.Status.QUEUED
                locked.save(
                    update_fields=["attempts", "error_code", "status", "updated_at"]
                )
                enqueue(
                    MESSAGE_DELIVER_JOB,
                    {"message_id": str(locked.id)},
                    due_at=moment + policy.retry_delay(locked.attempts),
                    idempotency_key=f"message-retry:{locked.id}:{locked.attempts}",
                    clinic=clinic,
                )
                record_audit(
                    "messaging.message_retry",
                    entity=locked,
                    after={**_snapshot(locked), "error_code": "adapter_error"},
                )
                return locked

            locked.status = Message.Status.SENT
            locked.provider = getattr(chosen, "name", "")
            locked.provider_message_id = provider_id
            locked.sent_at = moment
            locked.error_code = ""
            locked.save(
                update_fields=[
                    "attempts",
                    "status",
                    "provider",
                    "provider_message_id",
                    "sent_at",
                    "error_code",
                    "updated_at",
                ]
            )
            record_audit(
                "messaging.message_sent",
                entity=locked,
                after=_snapshot(locked),
            )
            return locked


@durable_task(MESSAGE_DELIVER_JOB, queue=MESSAGE_QUEUE)
def handle_deliver_message(payload: dict[str, Any], *, adapter: Any = None) -> dict[str, Any] | None:
    """Durable handler: deliver a deferred or retried message exactly once."""

    message_id = (payload or {}).get("message_id")
    if not message_id:
        return None
    message = Message.all_objects.filter(pk=message_id).first()
    if message is None:
        return None
    if message.status != Message.Status.QUEUED:
        return {"message_id": str(message.id), "status": message.status}

    with with_clinic(message.clinic_id):
        if suppression.purpose_is_blocked(message.purpose):
            digest = suppression.address_hash(
                message.channel,
                suppression.address_for(message.patient, message.channel),
            )
            if suppression.is_suppressed(message.clinic, message.channel, digest):
                _mark_skip(message, "suppressed")
                return {"message_id": str(message.id), "status": message.status}

    delivered = deliver(message, adapter=adapter)
    return {"message_id": str(delivered.id), "status": delivered.status}


__all__ = [
    "MESSAGE_DELIVER_JOB",
    "MESSAGE_QUEUE",
    "SKIP_REASONS",
    "deliver",
    "handle_deliver_message",
    "patient_skip_reason",
    "send",
]
