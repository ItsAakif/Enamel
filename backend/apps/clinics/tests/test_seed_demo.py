"""seed_demo command idempotency."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from apps.accounts.models import User
from apps.clinics.models import Clinic, Location, Membership

pytestmark = pytest.mark.django_db

DEMO_PASSWORD = "EnamelDemo!2026"


def run_seed() -> str:
    output = StringIO()
    call_command("seed_demo", stdout=output)
    return output.getvalue()


def test_seed_demo_is_idempotent():
    first = run_seed()

    assert Clinic.objects.count() == 2
    assert Location.all_objects.count() == 2
    assert User.objects.count() == 4
    assert Membership.all_objects.count() == 5
    assert DEMO_PASSWORD in first
    assert "owner@enamel.demo" in first

    second = run_seed()

    assert Clinic.objects.count() == 2
    assert Location.all_objects.count() == 2
    assert User.objects.count() == 4
    assert Membership.all_objects.count() == 5
    assert DEMO_PASSWORD not in second
    assert "No new demo users created." in second


def test_seed_demo_assigns_the_four_roles():
    run_seed()

    roles = set(
        Membership.all_objects.filter(clinic__slug="enamel-central").values_list(
            "role", flat=True
        )
    )
    assert roles == {"owner", "dentist", "receptionist", "assistant"}
    assert Location.all_objects.filter(clinic__slug="enamel-central").count() == 1
    assert Location.all_objects.filter(clinic__slug="enamel-north").count() == 1


def test_seed_demo_creates_the_documented_portal_patient_once():
    from apps.billing.models import Invoice
    from apps.patients.models import Patient
    from apps.scheduling.models import Appointment

    run_seed()
    run_seed()

    patient = Patient.all_objects.get(
        clinic__slug="enamel-central", email="patient@enamel.demo"
    )
    assert patient.first_name == "Demo"
    assert patient.last_name == "Patient"
    assert patient.phone_e164 == "+919800000001"
    assert patient.deleted_at is None

    # The documented two contacts resolve the same patient for the portal
    # (this is what the demo-mode request-code flow uses).
    assert Patient.all_objects.filter(
        clinic=patient.clinic, phone_e164="+919800000001"
    ).count() == 1

    # A realistic appointment and an issued invoice exist on the first run and
    # the second run adds nothing.
    assert Appointment.all_objects.filter(clinic=patient.clinic, patient=patient).count() == 1
    assert Invoice.all_objects.filter(clinic=patient.clinic, patient=patient).count() == 1
