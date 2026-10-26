"""Campaign approval, activation, pausing and resuming (T132).

Lifecycle::

    draft -> (approve) approved -> (activate) active -> completed
                                        |  ^
                                   (pause)  (activate = resume)
                                        v  |
                                      paused

Rules enforced here (never in a view):

* Activation requires ``approved_by`` to be set **and** to differ from
  ``created_by``; a campaign the creator approved alone can never be activated.
* Recipients are created once per ``(campaign, patient)``. Resuming a paused
  campaign only processes ``pending`` rows, so an already-sent patient is never
  messaged twice.
* Every send goes through :func:`apps.messaging.services.dispatch.send`, so
  consent, suppression, quiet hours and the frequency cap all still apply; a
  skipped patient is recorded with the dispatcher's reason.
* The clinic-local daily cap pauses the campaign (with an audit row) instead of
  exceeding the cap.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.tenancy import with_clinic
from apps.messaging.models import Consent, Message
from apps.messaging.services import dispatch

from ..models import Campaign, CampaignRecipient
from . import guardrails, segments

SNAPSHOT_FIELDS = (
    "name",
    "status",
    "channel",
    "daily_cap",
    "scheduled_at",
)

#: Message statuses that count as a delivered send intent for the recipient row.
_SENT_MESSAGE_STATUSES = frozenset(
    {Message.Status.QUEUED, Message.Status.SENT, Message.Status.DELIVERED}
)
_FAILED_MESSAGE_STATUSES = frozenset({Message.Status.FAILED, Message.Status.DEAD})


def snapshot(campaign: Campaign) -> dict[str, Any]:
    """Audit projection for a campaign (enums and counters only)."""

    return {
        **{name: getattr(campaign, name) for name in SNAPSHOT_FIELDS},
        "approved_by_id": str(campaign.approved_by_id) if campaign.approved_by_id else None,
        "approved_at": campaign.approved_at.isoformat() if campaign.approved_at else None,
    }


def recipient_status_for(message: Message) -> str:
    """Map a dispatcher ``Message`` onto a recipient status."""

    if message.status == Message.Status.SKIPPED:
        return CampaignRecipient.Status.SKIPPED
    if message.status in _FAILED_MESSAGE_STATUSES:
        return CampaignRecipient.Status.FAILED
    if message.status in _SENT_MESSAGE_STATUSES:
        return CampaignRecipient.Status.SENT
    return CampaignRecipient.Status.PENDING


def _assert_activation_allowed(campaign: Campaign) -> None:
    if campaign.status not in (
        Campaign.Status.APPROVED,
        Campaign.Status.PAUSED,
        Campaign.Status.ACTIVE,
    ):
        raise ApiError(
            "permission_denied",
            "The campaign must be approved before it can be activated.",
            status_code=403,
        )
    if (
        not campaign.approved_by_id
        or campaign.approved_by_id == campaign.created_by_id
    ):
        raise ApiError(
            "permission_denied",
            "A second approver must approve this campaign before activation.",
            status_code=403,
        )


def approve(
    campaign: Campaign,
    *,
    actor: Any,
    request: Any = None,
    now: datetime | None = None,
) -> Campaign:
    """Record a human approval (any user holding ``marketing.approve``)."""

    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(campaign.clinic):
            locked = Campaign.all_objects.select_for_update().get(pk=campaign.pk)
            if locked.status == Campaign.Status.COMPLETED:
                raise ApiError(
                    "conflict",
                    "A completed campaign cannot be re-approved.",
                    status_code=409,
                )
            before = snapshot(locked)
            locked.status = Campaign.Status.APPROVED
            locked.approved_by = actor
            locked.approved_at = moment
            locked.save(
                update_fields=["status", "approved_by", "approved_at", "updated_at"]
            )
            record_audit(
                "marketing.campaign_approved",
                entity=locked,
                before=before,
                after=snapshot(locked),
                actor=actor,
                request=request,
            )
    return locked


def ensure_recipients(campaign: Campaign, *, now: datetime | None = None) -> int:
    """Create the ``pending`` recipient rows for the campaign's segment.

    Idempotent: ``(campaign, patient)`` is unique, so a second call (a resume)
    creates nothing and returns ``0``.
    """

    rows = segments.resolve_rows(campaign.clinic, now=now)
    predicate = campaign.segment.predicate_json
    matched = [
        uuid.UUID(row["patient_id"])
        for row in rows
        if segments.evaluate(row["fields"], predicate)
    ]
    existing = set(
        CampaignRecipient.all_objects.filter(campaign=campaign).values_list(
            "patient_id", flat=True
        )
    )
    to_create = [pid for pid in matched if pid not in existing]
    if to_create:
        CampaignRecipient.all_objects.bulk_create(
            [
                CampaignRecipient(
                    clinic=campaign.clinic,
                    campaign=campaign,
                    patient_id=pid,
                    status=CampaignRecipient.Status.PENDING,
                )
                for pid in to_create
            ]
        )
    return len(to_create)


def _send_to_recipient(
    campaign: Campaign,
    recipient: CampaignRecipient,
    *,
    actor: Any,
    request: Any,
    moment: datetime,
) -> CampaignRecipient:
    template_key = campaign.template.key if campaign.template_id else ""
    message = dispatch.send(
        recipient.patient,
        campaign.channel,
        Consent.Purpose.MARKETING,
        template_key,
        context={
            "first_name": recipient.patient.first_name,
            "clinic": campaign.clinic.name,
        },
        idempotency_key=f"campaign:{campaign.pk}:{recipient.patient_id}",
        now=moment,
        actor=actor,
        request=request,
    )
    recipient.message = message
    recipient.status = recipient_status_for(message)
    recipient.skip_reason = (
        message.reason if recipient.status == CampaignRecipient.Status.SKIPPED else ""
    )
    recipient.save(update_fields=["message", "status", "skip_reason", "updated_at"])
    return recipient


def activate(
    campaign: Campaign,
    *,
    actor: Any,
    request: Any = None,
    now: datetime | None = None,
) -> Campaign:
    """Create recipients, send through the dispatcher and honour the daily cap."""

    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(campaign.clinic):
            locked = Campaign.all_objects.select_for_update().get(pk=campaign.pk)
            if locked.status in (Campaign.Status.ACTIVE, Campaign.Status.COMPLETED):
                # Idempotent: an in-flight or finished activation is not restarted.
                return locked

            _assert_activation_allowed(locked)
            ensure_recipients(locked, now=moment)

            cap = int(locked.daily_cap or 0)
            sent_today = guardrails.sends_today(locked, moment)
            paused_for_cap = False

            pending = (
                CampaignRecipient.all_objects.filter(
                    campaign=locked, status=CampaignRecipient.Status.PENDING
                )
                .select_related("patient")
                .order_by("created_at", "id")
            )
            for recipient in pending:
                if cap > 0 and sent_today >= cap:
                    paused_for_cap = True
                    break
                _send_to_recipient(
                    locked, recipient, actor=actor, request=request, moment=moment
                )
                if recipient.status == CampaignRecipient.Status.SENT:
                    sent_today += 1

            if paused_for_cap:
                locked.status = Campaign.Status.PAUSED
                locked.save(update_fields=["status", "updated_at"])
                record_audit(
                    "marketing.campaign_paused",
                    entity=locked,
                    after={**snapshot(locked), "reason": "daily_cap", "sends_today": sent_today},
                    actor=actor,
                    request=request,
                )
            else:
                remaining = CampaignRecipient.all_objects.filter(
                    campaign=locked, status=CampaignRecipient.Status.PENDING
                ).exists()
                locked.status = (
                    Campaign.Status.ACTIVE if remaining else Campaign.Status.COMPLETED
                )
                locked.save(update_fields=["status", "updated_at"])
                record_audit(
                    "marketing.campaign_activated",
                    entity=locked,
                    after={**snapshot(locked), "sends_today": sent_today},
                    actor=actor,
                    request=request,
                )
    return locked


def pause(
    campaign: Campaign,
    *,
    actor: Any,
    request: Any = None,
    reason: str = "manual",
) -> Campaign:
    """Stop further sends; a paused campaign can be resumed with ``activate``."""

    with transaction.atomic():
        with with_clinic(campaign.clinic):
            locked = Campaign.all_objects.select_for_update().get(pk=campaign.pk)
            if locked.status == Campaign.Status.PAUSED:
                return locked
            if locked.status not in (
                Campaign.Status.ACTIVE,
                Campaign.Status.APPROVED,
            ):
                raise ApiError(
                    "invalid_transition",
                    "Only an active campaign can be paused.",
                    status_code=409,
                    details={"status": locked.status},
                )
            before = snapshot(locked)
            locked.status = Campaign.Status.PAUSED
            locked.save(update_fields=["status", "updated_at"])
            record_audit(
                "marketing.campaign_paused",
                entity=locked,
                before=before,
                after={**snapshot(locked), "reason": reason},
                actor=actor,
                request=request,
            )
    return locked


__all__ = [
    "SNAPSHOT_FIELDS",
    "activate",
    "approve",
    "ensure_recipients",
    "pause",
    "recipient_status_for",
    "snapshot",
]
