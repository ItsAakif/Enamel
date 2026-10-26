"""Campaign sending, consent/suppression and counted metrics (T132)."""

from __future__ import annotations

import pytest

from apps.marketing.models import Campaign, CampaignRecipient
from apps.messaging.models import Message
from apps.messaging.services import suppression

pytestmark = pytest.mark.django_db

ACTIVE_PREDICATE = {"all": [{"field": "status", "op": "eq", "value": "active"}]}


def _make_segment(client, name="Active patients"):
    response = client.post(
        "/api/v1/segments/", {"name": name, "predicate_json": ACTIVE_PREDICATE}
    )
    assert response.status_code == 201, response.content
    return response.json()["id"]


def _make_campaign(client, segment_id, template_id, *, daily_cap=0):
    response = client.post(
        "/api/v1/campaigns/",
        {
            "name": "Spring promo",
            "segment": segment_id,
            "template": template_id,
            "channel": "email",
            "daily_cap": daily_cap,
        },
    )
    assert response.status_code == 201, response.content
    return response.json()["id"]


def _approve_and_activate(owner, approver, campaign_id):
    assert approver.post(f"/api/v1/campaigns/{campaign_id}/approve/").status_code == 200
    return owner.post(f"/api/v1/campaigns/{campaign_id}/activate/")


def test_activation_honours_consent_and_suppression(
    owner, member_factory, patient_factory, template_factory, grant_marketing_consent
):
    approver = member_factory(owner.clinic, role="receptionist")
    template = template_factory(owner.clinic)
    p_ok = patient_factory(owner.clinic, first_name="Ok", email="ok@example.test")
    p_sup = patient_factory(owner.clinic, first_name="Sup", email="sup@example.test")
    p_no = patient_factory(owner.clinic, first_name="No", email="no@example.test")
    grant_marketing_consent(p_ok)
    grant_marketing_consent(p_sup)
    suppression.record_opt_out(p_sup, channel="email", reason="stop")

    segment_id = _make_segment(owner)
    campaign_id = _make_campaign(owner, segment_id, str(template.id))
    response = _approve_and_activate(owner, approver, campaign_id)
    assert response.status_code == 200, response.content

    recipients = {
        row.patient_id: row
        for row in CampaignRecipient.all_objects.filter(campaign_id=campaign_id)
    }
    assert recipients[p_ok.id].status == CampaignRecipient.Status.SENT
    assert recipients[p_sup.id].status == CampaignRecipient.Status.SKIPPED
    assert recipients[p_sup.id].skip_reason == "suppressed"
    assert recipients[p_no.id].status == CampaignRecipient.Status.SKIPPED
    assert recipients[p_no.id].skip_reason == "no_consent"
    # Every skip is backed by a real Message row carrying the reason.
    assert Message.all_objects.filter(
        patient_id=p_sup.id, status=Message.Status.SKIPPED, reason="suppressed"
    ).exists()


def test_metrics_equal_source_counts(
    owner, member_factory, patient_factory, template_factory, grant_marketing_consent
):
    approver = member_factory(owner.clinic, role="receptionist")
    template = template_factory(owner.clinic)
    p_ok = patient_factory(owner.clinic, first_name="Ok", email="ok@example.test")
    patient_factory(owner.clinic, first_name="No", email="no@example.test")
    grant_marketing_consent(p_ok)

    segment_id = _make_segment(owner)
    campaign_id = _make_campaign(owner, segment_id, str(template.id))
    _approve_and_activate(owner, approver, campaign_id)

    metrics = owner.get(f"/api/v1/campaigns/{campaign_id}/metrics/").json()

    recipients = CampaignRecipient.all_objects.filter(campaign_id=campaign_id)
    assert metrics["recipients"]["total"] == recipients.count()
    for status in ("pending", "sent", "skipped", "failed"):
        assert metrics["recipients"][status] == recipients.filter(status=status).count()

    message_ids = list(
        recipients.exclude(message__isnull=True).values_list("message_id", flat=True)
    )
    messages = Message.all_objects.filter(id__in=message_ids)
    assert metrics["messages"]["total"] == messages.count()
    for status in ("queued", "sent", "delivered", "skipped"):
        assert metrics["messages"][status] == messages.filter(status=status).count()
    assert metrics["messages"]["failed"] == messages.filter(
        status__in=[Message.Status.FAILED, Message.Status.DEAD]
    ).count()

    # No provider events were reported, so none are invented.
    assert metrics["provider_events"] == {}
    assert metrics["engagement"]["open_rate"] is None
    assert metrics["engagement"]["available"] is False


def test_pause_then_resume_does_not_duplicate(
    owner, member_factory, patient_factory, template_factory, grant_marketing_consent
):
    approver = member_factory(owner.clinic, role="receptionist")
    template = template_factory(owner.clinic)
    patients = [patient_factory(owner.clinic, first_name=f"P{index}") for index in range(5)]
    for patient in patients:
        grant_marketing_consent(patient)

    segment_id = _make_segment(owner)
    campaign_id = _make_campaign(owner, segment_id, str(template.id), daily_cap=2)

    first = _approve_and_activate(owner, approver, campaign_id)
    assert first.status_code == 200
    assert first.json()["status"] == "paused"
    assert (
        CampaignRecipient.all_objects.filter(
            campaign_id=campaign_id, status=CampaignRecipient.Status.SENT
        ).count()
        == 2
    )

    # Pausing again is idempotent and sends nothing more.
    paused = owner.post(f"/api/v1/campaigns/{campaign_id}/pause/")
    assert paused.status_code == 200
    assert (
        CampaignRecipient.all_objects.filter(
            campaign_id=campaign_id, status=CampaignRecipient.Status.SENT
        ).count()
        == 2
    )

    # Lift the cap and resume: the remaining pending patients are sent once.
    Campaign.all_objects.filter(pk=campaign_id).update(daily_cap=0)
    resumed = owner.post(f"/api/v1/campaigns/{campaign_id}/activate/")
    assert resumed.status_code == 200, resumed.content
    assert resumed.json()["status"] == "completed"

    recipients = CampaignRecipient.all_objects.filter(campaign_id=campaign_id)
    assert recipients.count() == 5
    assert recipients.values("patient_id").distinct().count() == 5
    assert recipients.filter(status=CampaignRecipient.Status.SENT).count() == 5
    # Exactly one message per patient: resume never duplicated a send.
    for patient in patients:
        assert (
            Message.all_objects.filter(patient_id=patient.id, purpose="marketing").count()
            == 1
        )
