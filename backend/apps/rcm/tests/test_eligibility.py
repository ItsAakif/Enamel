"""Policy resolution and deterministic eligibility (T134)."""

from __future__ import annotations

from datetime import date

import pytest

from apps.rcm.adapters.mock_clearinghouse import MockClearinghouse
from apps.rcm.models import EligibilityCheck
from apps.rcm.services import eligibility

pytestmark = pytest.mark.django_db


def test_policy_status_classifies_the_window(payer_factory, policy_factory, patient_factory, owner):
    patient = patient_factory(owner.clinic)
    payer = payer_factory(owner.clinic)
    policy = policy_factory(
        owner.clinic,
        patient,
        payer,
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )
    assert eligibility.policy_status(policy, date(2026, 6, 1)) == "active"
    assert eligibility.policy_status(policy, date(2025, 12, 31)) == "not_yet_effective"
    assert eligibility.policy_status(policy, date(2027, 1, 1)) == "expired"
    policy.is_active = False
    assert eligibility.policy_status(policy, date(2026, 6, 1)) == "inactive"


def test_resolve_policy_picks_the_covering_policy(payer_factory, policy_factory, patient_factory, owner):
    patient = patient_factory(owner.clinic)
    payer = payer_factory(owner.clinic)
    policy_factory(
        owner.clinic,
        patient,
        payer,
        member_id="OLD",
        valid_from=date(2020, 1, 1),
        valid_to=date(2020, 12, 31),
    )
    current = policy_factory(
        owner.clinic,
        patient,
        payer,
        member_id="NEW",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )
    resolved = eligibility.resolve_policy(patient, date(2026, 6, 1))
    assert resolved is not None
    assert resolved.member_id == "NEW"

    # No cover for 2023: resolution returns None but the latest is still flagged.
    assert eligibility.resolve_policy(patient, date(2023, 6, 1)) is None
    latest = eligibility.latest_policy(patient)
    assert latest is not None and latest.member_id == current.member_id
    assert eligibility.policy_status(latest, date(2023, 6, 1)) == "not_yet_effective"


def test_eligibility_endpoint_is_deterministic(owner, patient_factory, payer_factory, policy_factory):
    patient = patient_factory(owner.clinic)
    payer = payer_factory(owner.clinic)
    policy = policy_factory(
        owner.clinic,
        patient,
        payer,
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )
    first = owner.post(
        "/api/v1/eligibility-checks/",
        {"policy": str(policy.id), "service_date": "2026-06-01"},
    )
    assert first.status_code == 201, first.content
    body = first.json()
    assert body["status"] == "eligible"
    assert body["error_code"] == ""
    assert body["response_json"]["coverage_percent"] == 80

    # The adapter is a pure function of its input.
    adapter = MockClearinghouse()
    assert adapter.check_eligibility(policy, date(2026, 6, 1)).response == adapter.check_eligibility(
        policy, date(2026, 6, 1)
    ).response


def test_expired_policy_check_stores_the_failure(owner, patient_factory, payer_factory, policy_factory):
    patient = patient_factory(owner.clinic)
    payer = payer_factory(owner.clinic)
    policy = policy_factory(
        owner.clinic,
        patient,
        payer,
        valid_from=date(2020, 1, 1),
        valid_to=date(2020, 12, 31),
    )
    response = owner.post(
        "/api/v1/eligibility-checks/",
        {"policy": str(policy.id), "service_date": "2026-06-01"},
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["status"] == "ineligible"
    assert body["error_code"] == "policy_expired"
    stored = EligibilityCheck.all_objects.get(pk=body["id"])
    assert stored.error_code == "policy_expired"


def test_policy_missing_is_an_error_result():
    adapter = MockClearinghouse()
    result = adapter.check_eligibility(None, date(2026, 6, 1))
    assert result.status == "error"
    assert result.error_code == "policy_missing"


def test_eligibility_check_requires_manage(assistant, owner, patient_factory, payer_factory, policy_factory):
    patient = patient_factory(owner.clinic)
    payer = payer_factory(owner.clinic)
    policy = policy_factory(owner.clinic, patient, payer)
    response = assistant.post(
        "/api/v1/eligibility-checks/",
        {"policy": str(policy.id), "service_date": "2026-06-01"},
    )
    assert response.status_code == 403
