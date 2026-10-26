"""Campaign delivery metrics (T132).

Metrics are *counted*, never synthesised. The report is derived entirely from
``CampaignRecipient`` rows and the ``messaging.Message`` / ``ProviderEvent``
rows they point at:

* recipient counts by status,
* message counts by status,
* provider-event counts by type (as reported by the adapter, never invented).

There is deliberately no open/read rate: an adapter that cannot report opens
must not have one fabricated for it. The ``engagement`` block states this
explicitly so a UI cannot render a misleading number.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Count

from apps.messaging.models import Message, ProviderEvent

from ..models import CampaignRecipient

ENGAGEMENT_NOTE = (
    "Open and read rates are not synthesised; only provider-reported counts are shown."
)


def _status_counts(queryset, field: str) -> dict[str, int]:
    return {
        str(value): int(count)
        for value, count in queryset.values_list(field).annotate(c=Count("id"))
    }


def report(campaign: Any) -> dict[str, Any]:
    """Return the counted delivery metrics for one campaign."""

    recipients = CampaignRecipient.all_objects.filter(campaign_id=campaign.pk)
    recipient_counts = _status_counts(recipients, "status")
    recipient_total = recipients.count()

    message_ids = list(
        recipients.exclude(message__isnull=True).values_list("message_id", flat=True)
    )
    messages = Message.all_objects.filter(id__in=message_ids)
    message_counts = _status_counts(messages, "status")
    provider_events = {
        str(event_type): int(count)
        for event_type, count in ProviderEvent.all_objects.filter(
            message_id__in=message_ids
        )
        .values_list("event_type")
        .annotate(c=Count("id"))
    }

    return {
        "campaign_id": str(campaign.pk),
        "status": campaign.status,
        "recipients": {
            "total": recipient_total,
            "pending": recipient_counts.get(CampaignRecipient.Status.PENDING, 0),
            "sent": recipient_counts.get(CampaignRecipient.Status.SENT, 0),
            "skipped": recipient_counts.get(CampaignRecipient.Status.SKIPPED, 0),
            "failed": recipient_counts.get(CampaignRecipient.Status.FAILED, 0),
        },
        "messages": {
            "total": messages.count(),
            "queued": message_counts.get(Message.Status.QUEUED, 0),
            "sent": message_counts.get(Message.Status.SENT, 0),
            "delivered": message_counts.get(Message.Status.DELIVERED, 0),
            "failed": message_counts.get(Message.Status.FAILED, 0)
            + message_counts.get(Message.Status.DEAD, 0),
            "skipped": message_counts.get(Message.Status.SKIPPED, 0),
        },
        "provider_events": provider_events,
        "engagement": {
            "available": False,
            "open_rate": None,
            "note": ENGAGEMENT_NOTE,
        },
    }


__all__ = ["ENGAGEMENT_NOTE", "report"]
