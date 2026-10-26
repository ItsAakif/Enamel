"""Seed synthetic RCM fixtures covering every claim state (T134).

Creates (or reuses) a ``rcm-fixtures`` clinic and, idempotently, one claim per
state: draft, validated, submitted, accepted, denied (with its correction) and
paid (with a posted remittance that zeroes the linked invoice balance). All data
is synthetic; no real payer, member id or patient is used.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.billing.models import Invoice
from apps.clinics.models import Clinic
from apps.core.rls import bypass_rls, require_bypass_capable
from apps.core.tenancy import with_clinic
from apps.patients.models import Patient
from apps.rcm.models import Claim, InsurancePolicy, Payer
from apps.rcm.services import claims as claim_service
from apps.rcm.services import remittance as remittance_service

CLINIC_SLUG = "rcm-fixtures"
PAYER_CODE = "MOCK-INS"
MEMBER_ID = "MOCK-0001"
#: (patient number, claim status) -- one fixture per state.
STATES: tuple[tuple[str, str], ...] = (
    ("RCM-DRAFT", Claim.Status.DRAFT),
    ("RCM-VALIDATED", Claim.Status.VALIDATED),
    ("RCM-SUBMITTED", Claim.Status.SUBMITTED),
    ("RCM-ACCEPTED", Claim.Status.ACCEPTED),
    ("RCM-DENIED", Claim.Status.DENIED),
    ("RCM-PAID", Claim.Status.PAID),
)
LINE = {"procedure_code": "D1110", "tooth": None, "fee_minor": 5000, "units": 1}


class Command(BaseCommand):
    help = "Seed synthetic RCM fixtures covering every claim state."

    @transaction.atomic
    @bypass_rls()
    def handle(self, *args: Any, **options: Any) -> None:
        require_bypass_capable("seed_rcm_fixtures")
        clinic, clinic_created = Clinic.objects.get_or_create(
            slug=CLINIC_SLUG,
            defaults={
                "name": "RCM Fixtures",
                "timezone": "Asia/Kolkata",
                "currency": "USD",
                "country": "US",
            },
        )
        created_claims = 0

        with with_clinic(clinic):
            payer, _ = Payer.all_objects.get_or_create(
                clinic=clinic,
                code=PAYER_CODE,
                defaults={"name": "Mock Insurance", "is_active": True},
            )

            for number, target_status in STATES:
                patient, _ = Patient.all_objects.get_or_create(
                    clinic=clinic,
                    number=number,
                    defaults={
                        "first_name": "Fixture",
                        "last_name": number.title(),
                    },
                )
                policy, _ = InsurancePolicy.all_objects.get_or_create(
                    clinic=clinic,
                    patient=patient,
                    payer=payer,
                    member_id=MEMBER_ID,
                    defaults={
                        "plan_name": "Mock PPO",
                        "valid_from": date(2020, 1, 1),
                        "is_active": True,
                    },
                )
                if Claim.all_objects.filter(clinic=clinic, patient=patient).exists():
                    continue

                invoice = None
                if target_status == Claim.Status.PAID:
                    invoice = Invoice.all_objects.create(
                        clinic=clinic,
                        patient=patient,
                        number=f"RCM-{number}",
                        status=Invoice.Status.ISSUED,
                        currency="USD",
                        subtotal_minor=LINE["fee_minor"],
                        total_minor=LINE["fee_minor"],
                        balance_minor=LINE["fee_minor"],
                        issued_at=timezone.now(),
                    )

                claim = claim_service.create_claim(
                    clinic,
                    patient=patient,
                    lines=[dict(LINE)],
                    policy=policy,
                    invoice=invoice,
                    diagnosis_codes=["K02.9"],
                    status=target_status,
                )

                if target_status == Claim.Status.SUBMITTED:
                    claim.external_ref = f"MOCK-{number}"
                    claim.submitted_at = timezone.now()
                    claim.save(update_fields=["external_ref", "submitted_at", "updated_at"])
                elif target_status == Claim.Status.ACCEPTED:
                    claim.external_ref = f"MOCK-{number}"
                    claim.submitted_at = timezone.now()
                    claim.save(update_fields=["external_ref", "submitted_at", "updated_at"])
                elif target_status == Claim.Status.DENIED:
                    claim.external_ref = f"MOCK-{number}"
                    claim.submitted_at = timezone.now()
                    claim.save(update_fields=["external_ref", "submitted_at", "updated_at"])
                    claim_service.mark_denied(claim, reason="synthetic denial")
                elif target_status == Claim.Status.PAID:
                    claim.external_ref = f"MOCK-{number}"
                    claim.submitted_at = timezone.now()
                    claim.save(update_fields=["external_ref", "submitted_at", "updated_at"])
                    remittance_service.post(
                        clinic,
                        claim=claim,
                        payer_ref=f"REM-{number}",
                        paid_minor=LINE["fee_minor"],
                        status="posted",
                    )

                created_claims += 1

        self.stdout.write(
            f"RCM fixtures clinic {'created' if clinic_created else 'reused'}; "
            f"claims created: {created_claims}."
        )
