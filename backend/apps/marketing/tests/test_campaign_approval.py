"""Campaign approval and activation separation of duties (T132)."""

from __future__ import annotations

import pytest

from apps.core.models import AuditEvent
from apps.marketing.models import CampaignRecipient

pytestmark = pytest.mark.django_db

ACTIVE_PREDICATE = {"all": [{"field": "status", "op": "eq", "value": "active"}]}


def _make_segment(client, name="Active patients"):
    response = client.post(
        "/api/v1/segments/", {"name": name, "predicate_json": ACTIVE_PREDICATE}
    )
    assert response.status_code == 201, response.content
    return response.json()["id"]


def _make_campaign(client, segment_id, template_id, *, daily_cap=0, channel="email"):
    response = client.post(
        "/api/v1/campaigns/",
        {
            "name": "Spring promo",
            "segment": segment_id,
            "template": template_id,
            "channel": channel,
            "daily_cap": daily_cap,
        },
    )
    assert response.status_code == 201, response.content
    return response.json()["id"]


def test_creator_only_activation_is_403_then_second_approver_succeeds(
    owner, member_factory, patient_factory, template_factory, grant_marketing_consent
):
    approver = member_factory(owner.clinic, role="receptionist")
    template = template_factory(owner.clinic)
    p1 = patient_factory(owner.clinic, first_name="One")
    p2 = patient_factory(owner.clinic, first_name="Two")
    grant_marketing_consent(p1)
    grant_marketing_consent(p2)

    segment_id = _make_segment(owner)
    campaign_id = _make_campaign(owner, segment_id, str(template.id))

    # Not approved yet: activation is refused.
    denied = owner.post(f"/api/v1/campaigns/{campaign_id}/activate/")
    assert denied.status_code == 403, denied.content

    # The creator may record an approval, but self-approval cannot activate.
    approved = owner.post(f"/api/v1/campaigns/{campaign_id}/approve/")
    assert approved.status_code == 200, approved.content
    assert approved.json()["approved_by"] == str(owner.user.id)

    self_activation = owner.post(f"/api/v1/campaigns/{campaign_id}/activate/")
    assert self_activation.status_code == 403, self_activation.content

    # A second approver unlocks activation.
    second = approver.post(f"/api/v1/campaigns/{campaign_id}/approve/")
    assert second.status_code == 200, second.content
    assert second.json()["approved_by"] == str(approver.user.id)

    activated = owner.post(f"/api/v1/campaigns/{campaign_id}/activate/")
    assert activated.status_code == 200, activated.content
    assert activated.json()["status"] in {"active", "completed"}
    assert CampaignRecipient.all_objects.filter(campaign_id=campaign_id).count() == 2


def test_activation_records_audit_rows(
    owner, member_factory, patient_factory, template_factory, grant_marketing_consent
):
    approver = member_factory(owner.clinic, role="receptionist")
    template = template_factory(owner.clinic)
    patient = patient_factory(owner.clinic)
    grant_marketing_consent(patient)

    segment_id = _make_segment(owner)
    campaign_id = _make_campaign(owner, segment_id, str(template.id))

    approver.post(f"/api/v1/campaigns/{campaign_id}/approve/")
    owner.post(f"/api/v1/campaigns/{campaign_id}/activate/")

    actions = set(
        AuditEvent.objects.filter(clinic=owner.clinic).values_list("action", flat=True)
    )
    assert "marketing.campaign_created" in actions
    assert "marketing.campaign_approved" in actions
    assert "marketing.campaign_activated" in actions


def test_cross_clinic_campaign_is_404(owner, patient_factory, template_factory, staff_factory):
    other = staff_factory(role="owner")
    template = template_factory(other.clinic)
    segment_id = _make_segment(other, name="Other")
    campaign_id = _make_campaign(other, segment_id, str(template.id))

    assert owner.post(f"/api/v1/campaigns/{campaign_id}/approve/").status_code == 404
    assert owner.post(f"/api/v1/campaigns/{campaign_id}/activate/").status_code == 404
    assert owner.get(f"/api/v1/campaigns/{campaign_id}/metrics/").status_code == 404


def test_activation_requires_approve_permission(owner, assistant, member_factory, patient_factory, template_factory):
    # An assistant cannot reach the activation endpoint at all.
    template = template_factory(owner.clinic)
    segment_id = _make_segment(owner)
    campaign_id = _make_campaign(owner, segment_id, str(template.id))
    assert assistant.post(f"/api/v1/campaigns/{campaign_id}/activate/").status_code == 403
