"""Synthetic RCM fixture seed command (T134)."""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.clinics.models import Clinic
from apps.rcm.models import Claim, Remittance

pytestmark = pytest.mark.django_db

EXPECTED_STATES = {"draft", "validated", "submitted", "accepted", "denied", "paid"}


def test_seed_command_creates_every_claim_state():
    call_command("seed_rcm_fixtures")

    clinic = Clinic.objects.get(slug="rcm-fixtures")
    statuses = set(
        Claim.all_objects.filter(clinic=clinic).values_list("status", flat=True)
    )
    assert EXPECTED_STATES <= statuses

    denied = Claim.all_objects.filter(clinic=clinic, status="denied").first()
    assert denied is not None
    assert Claim.all_objects.filter(correction_of=denied).exists()

    paid = Claim.all_objects.filter(clinic=clinic, status="paid").first()
    assert paid is not None
    assert Remittance.all_objects.filter(claim=paid).exists()
    assert paid.invoice.balance_minor == 0


def test_seed_command_is_idempotent():
    call_command("seed_rcm_fixtures")
    clinic = Clinic.objects.get(slug="rcm-fixtures")
    first = Claim.all_objects.filter(clinic=clinic).count()

    call_command("seed_rcm_fixtures")
    assert Claim.all_objects.filter(clinic=clinic).count() == first
    # Six fixtures plus one correction claim.
    assert first == 7
