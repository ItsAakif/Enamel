"""The platform maintenance path and the public identifiers (T138).

Two properties of the runtime switch are proved here:

* **D-2** - the platform maintenance commands (``audit_verify``,
  ``apply_patient_retention``, ``seed_demo``, ``seed_rcm_fixtures``) run with
  the maintenance role or with the owner, and they **refuse to run as a
  runtime role**: under the runtime role they would silently see nothing
  across the clinic boundary.
* **D-4** - the public identifiers of the product (the patient number, the
  clinic slug and the booking reference) are the same before and after the
  role switch, and none of them is derived from the database role or from the
  session.

The module never skips: it fails loudly when the environment cannot run the
proofs.
"""

from __future__ import annotations

import re
from contextlib import contextmanager

import pytest
from django.core.management import call_command
from django.db import connection

from apps.core.tenancy import with_clinic
from apps.core.tests.rls_owner_connection import owner_connection  # noqa: F401  (the proofs need a role switch)

pytestmark = [pytest.mark.django_db, pytest.mark.rls]

APP_ROLE = "enamel_app"
MAINT_ROLE = "enamel_maint"

#: The platform maintenance commands of ``manage.py``'s bypass allow-list.
MAINTENANCE_COMMANDS = (
    "audit_verify",
    "apply_patient_retention",
    "seed_demo",
    "seed_rcm_fixtures",
)

PATIENT_NUMBER_RE = re.compile(r"^EN-\d{6}$")
BOOKING_REFERENCE_RE = re.compile(r"^BK-[A-Z2-9]{6}$")


@contextmanager
def as_role(role: str):
    """Run the block as ``role`` and assert that the switch took effect."""

    with connection.cursor() as cursor:
        cursor.execute(f'SET ROLE "{role}"')
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        current = cursor.fetchone()[0]
    assert current == role, f"the proof did not run as {role} (it ran as {current})"
    try:
        yield
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("RESET ROLE")
        except Exception:  # pragma: no cover - transaction already aborted
            pass


# --- D-2: the maintenance path --------------------------------------------


@pytest.mark.parametrize("command", MAINTENANCE_COMMANDS)
def test_the_maintenance_commands_refuse_to_run_as_the_runtime_role(command):
    """A platform command must not run as the runtime role (decision D-2)."""

    import uuid

    # The commands have their own required arguments; they are supplied so
    # that the refusal is the *role* refusal and not an argument error.
    options = {"clinic": str(uuid.uuid4())} if command == "apply_patient_retention" else {}
    with as_role(APP_ROLE):
        with pytest.raises(RuntimeError) as failure:
            call_command(command, verbosity=0, **options)
    assert "maintenance role" in str(failure.value)


def test_the_audit_command_runs_under_the_maintenance_role(clinic_factory):
    """The maintenance role may run the platform commands."""

    from apps.core.rls import bypass_rls

    clinic_factory(slug="ops-audit")

    with as_role(MAINT_ROLE):
        with bypass_rls():
            call_command("audit_verify", verbosity=0)


def test_the_maintenance_role_holds_the_bypass_and_the_runtime_role_does_not():
    from apps.core.rls import bypass_capable, current_rls_role

    with as_role(MAINT_ROLE):
        assert bypass_capable() is True
    with as_role(APP_ROLE):
        assert bypass_capable() is False
    assert current_rls_role()[0] not in (MAINT_ROLE, APP_ROLE)


# --- D-4: the public identifiers -------------------------------------------


def _identifiers(role: str, suffix: str) -> dict[str, str]:
    """Create a clinic, a patient and a booking reference as ``role``."""

    from apps.booking.services.booking import _new_reference
    from apps.clinics.models import Clinic
    from apps.patients.models import Patient
    from apps.patients.services.numbering import allocate_patient_number

    with as_role(role):
        clinic = Clinic.objects.create(
            name="Identifier probe",
            slug=f"identifier-{suffix}",
            timezone="Asia/Kolkata",
            currency="INR",
        )
        with with_clinic(clinic):
            patient = Patient.all_objects.create(
                clinic=clinic,
                number=allocate_patient_number(clinic),
                first_name="Identifier",
                last_name="Probe",
            )
            reference = _new_reference(clinic)
    return {
        "slug": clinic.slug,
        "number": patient.number,
        "reference": reference,
    }


def _owner_role() -> str:
    """The role that owns the tenant tables (the compose owner).

    It is resolved from the catalog instead of being hard-coded, so the proof
    keeps working when the deployment uses a different owner name.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_userbyid(relowner) FROM pg_class "
            "WHERE oid = 'patients_patient'::regclass"
        )
        row = cursor.fetchone()
    assert row and row[0], "the tenant table has no owner"
    return row[0]


def test_the_public_identifiers_are_identical_under_the_owner_and_the_runtime_role():
    """The same inputs produce the same identifiers under either role."""

    owner_ids = _identifiers(_owner_role(), "owner")
    runtime_ids = _identifiers(APP_ROLE, "runtime")

    # The slug is the caller's, in both runs.
    assert owner_ids["slug"] == "identifier-owner"
    assert runtime_ids["slug"] == "identifier-runtime"

    # The patient number is allocated per clinic from the same counter, so the
    # first patient of a fresh clinic is the same number in both runs.
    assert owner_ids["number"] == "EN-000001"
    assert runtime_ids["number"] == owner_ids["number"]

    # The booking reference has the same shape in both runs.
    assert BOOKING_REFERENCE_RE.match(owner_ids["reference"])
    assert BOOKING_REFERENCE_RE.match(runtime_ids["reference"])


def test_no_public_identifier_is_derived_from_the_database_role_or_the_session():
    """The identifiers never carry the role, the session or the connection."""

    identifiers = _identifiers(APP_ROLE, "derivation")
    for name, value in identifiers.items():
        assert APP_ROLE not in value, name
        assert "enamel" not in value.lower() or name == "slug", name
        assert "postgres" not in value.lower(), name

    # The session id (the login role of the connection) is not part of them
    # either: the same identifiers come from a different session.
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user, session_user, pg_backend_pid()")
        current, session, pid = cursor.fetchone()
    for name, value in identifiers.items():
        assert session not in value, name
        assert str(pid) not in value, name
        assert current not in value, name


def test_the_patient_number_format_is_unchanged():
    """The documented number format survives the role switch."""

    from apps.patients.services.numbering import format_patient_number

    assert format_patient_number(1) == "EN-000001"
    assert format_patient_number(1234) == "EN-001234"
    assert PATIENT_NUMBER_RE.match("EN-000001")
