"""Marketing guardrails: field allowlist and the daily cap (T132)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.marketing.models import Campaign, CampaignRecipient
from apps.marketing.services import guardrails
from apps.marketing.services.guardrails import ALLOWED_FIELDS, PredicateError

pytestmark = pytest.mark.django_db

ACTIVE_PREDICATE = {"all": [{"field": "status", "op": "eq", "value": "active"}]}


def test_allowlist_is_exactly_the_non_clinical_fields():
    assert set(ALLOWED_FIELDS) == {
        "status",
        "last_visit_days",
        "recall_due",
        "plan_status",
        "location",
        "source",
    }


@pytest.mark.parametrize(
    "field",
    ["diagnosis_codes", "clinical_notes", "allergies", "medical_history", "tooth", "procedure_code"],
)
def test_clinical_fields_are_rejected(field):
    with pytest.raises(PredicateError) as excinfo:
        guardrails.check_field(field)
    assert "clinical" in str(excinfo.value).lower()


def test_unsupported_non_clinical_field_is_rejected():
    with pytest.raises(PredicateError) as excinfo:
        guardrails.check_field("loyalty_tier")
    assert "unsupported" in str(excinfo.value).lower()


def test_clinic_day_bounds_are_local():
    clinic = type("C", (), {"timezone": "Asia/Kolkata"})()
    moment = datetime(2026, 6, 1, 20, 0, tzinfo=UTC)  # 01:30 next day in IST
    start, end = guardrails.clinic_day_bounds(clinic, moment)
    assert start <= moment < end
    assert (end - start) == timedelta(days=1)


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


def test_daily_cap_pauses_the_campaign_and_writes_an_audit_row(
    owner, member_factory, patient_factory, template_factory, grant_marketing_consent
):
    approver = member_factory(owner.clinic, role="receptionist")
    template = template_factory(owner.clinic)
    for index in range(5):
        patient = patient_factory(owner.clinic, first_name=f"Cap{index}")
        grant_marketing_consent(patient)

    segment_id = _make_segment(owner)
    campaign_id = _make_campaign(owner, segment_id, str(template.id), daily_cap=2)

    assert approver.post(f"/api/v1/campaigns/{campaign_id}/approve/").status_code == 200
    response = owner.post(f"/api/v1/campaigns/{campaign_id}/activate/")
    assert response.status_code == 200, response.content
    assert response.json()["status"] == "paused"

    recipients = CampaignRecipient.all_objects.filter(campaign_id=campaign_id)
    assert recipients.filter(status=CampaignRecipient.Status.SENT).count() == 2
    assert recipients.filter(status=CampaignRecipient.Status.PENDING).count() == 3

    audit = AuditEvent.objects.filter(
        action="marketing.campaign_paused", entity_id=str(campaign_id)
    ).first()
    assert audit is not None
    assert audit.after["reason"] == "daily_cap"
    assert audit.after["sends_today"] == 2


def test_sends_today_counts_only_this_campaign(
    owner, patient_factory, template_factory
):
    template = template_factory(owner.clinic)
    segment_id = _make_segment(owner)
    campaign_id = _make_campaign(owner, segment_id, str(template.id))
    campaign = Campaign.all_objects.get(pk=campaign_id)
    assert guardrails.sends_today(campaign, timezone.now()) == 0
