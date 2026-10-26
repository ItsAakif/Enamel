"""Clinical retention: dry run, purge, audit retention and the command (T106)."""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.patients.models import Encounter, Patient
from apps.patients.services.retention import (
    DEFAULT_CLINICAL_YEARS,
    apply_retention,
    retention_candidates,
    retention_config,
)

pytestmark = pytest.mark.django_db


def _patient_with_last_visit(clinic, patient_factory, days_ago: int, **extra):
    patient = patient_factory(clinic, **extra)
    Encounter.all_objects.create(
        clinic=clinic,
        patient=patient,
        occurred_at=timezone.now() - timedelta(days=days_ago),
    )
    return patient


def test_default_policy_is_eight_years():
    from apps.clinics.models import Clinic

    clinic = Clinic(name="Retention", slug="retention-default", retention_json={})
    assert retention_config(clinic) == {"clinical_years": 8, "source": "default"}
    assert DEFAULT_CLINICAL_YEARS == 8


def test_dry_run_reports_candidates_without_deleting(clinic_factory, patient_factory):
    clinic = clinic_factory(slug="retention-dry")
    old = _patient_with_last_visit(clinic, patient_factory, days_ago=3000, last_name="Old")
    recent = _patient_with_last_visit(clinic, patient_factory, days_ago=10, last_name="Recent")
    never_seen = patient_factory(clinic, last_name="Never")

    result = apply_retention(clinic, dry_run=True)

    assert result["dry_run"] is True
    assert result["retention"]["clinical_years"] == 8
    assert [row["id"] for row in result["candidates"]] == [str(old.id)]
    assert result["purged"] == 0

    assert Patient.all_objects.filter(clinic=clinic).count() == 3
    assert Encounter.all_objects.filter(patient=old).count() == 1
    # Read-only: no purge audit rows are written by a dry run.
    assert not AuditEvent.objects.filter(clinic=clinic, action="patient.purged").exists()
    assert recent.id != never_seen.id


def test_apply_purges_and_audits_without_deleting_audit_rows(clinic_factory, patient_factory):
    clinic = clinic_factory(slug="retention-apply")
    old = _patient_with_last_visit(clinic, patient_factory, days_ago=3000, last_name="Old")
    keep = _patient_with_last_visit(clinic, patient_factory, days_ago=5, last_name="Keep")
    created_audits = AuditEvent.objects.filter(clinic=clinic).count()

    result = apply_retention(clinic, dry_run=False)

    assert result["purged"] == 1
    assert Patient.all_objects.filter(clinic=clinic).count() == 1
    assert Patient.all_objects.filter(pk=old.pk).exists() is False
    assert Encounter.all_objects.filter(patient=keep).count() == 1

    purge_audit = AuditEvent.objects.get(action="patient.purged", entity_id=str(old.id))
    assert purge_audit.after["number"] == old.number
    # Audit rows survive the purge.
    assert AuditEvent.objects.filter(clinic=clinic).count() == created_audits + 1


def test_retention_json_shortens_the_window(clinic_factory, patient_factory):
    clinic = clinic_factory(
        slug="retention-short",
        retention_json={"clinical_retention_days": 30},
    )
    assert retention_config(clinic) == {"clinical_days": 30, "source": "retention_json"}

    eligible = _patient_with_last_visit(clinic, patient_factory, days_ago=60)
    kept = _patient_with_last_visit(clinic, patient_factory, days_ago=5)

    candidates = retention_candidates(clinic)
    assert [row["id"] for row in candidates] == [str(eligible.id)]
    assert str(kept.id) not in {row["id"] for row in candidates}


def test_last_visit_not_record_creation_drives_the_window(clinic_factory, patient_factory):
    clinic = clinic_factory(slug="retention-last-visit")
    patient = patient_factory(clinic, last_name="Recent")
    long_ago = timezone.now() - timedelta(days=4000)
    Patient.all_objects.filter(pk=patient.pk).update(created_at=long_ago, updated_at=long_ago)

    # No encounters at all: the (stale) record update is the last activity.
    assert [row["id"] for row in retention_candidates(clinic)] == [str(patient.id)]

    Encounter.all_objects.create(clinic=clinic, patient=patient, occurred_at=timezone.now())
    assert retention_candidates(clinic) == []


def test_retention_ignores_soft_deleted_patients(clinic_factory, patient_factory):
    clinic = clinic_factory(slug="retention-deleted")
    patient = _patient_with_last_visit(clinic, patient_factory, days_ago=3000)
    Patient.all_objects.filter(pk=patient.pk).update(
        deleted_at=timezone.now(), status=Patient.Status.ARCHIVED
    )
    assert retention_candidates(clinic) == []


def test_management_command_dry_run_then_apply(clinic_factory, patient_factory):
    clinic = clinic_factory(slug="retention-command")
    patient = _patient_with_last_visit(clinic, patient_factory, days_ago=3000)

    dry = io.StringIO()
    call_command("apply_patient_retention", "--clinic", str(clinic.id), stdout=dry)
    output = dry.getvalue()
    assert "dry run" in output
    assert patient.number in output
    assert Patient.all_objects.filter(pk=patient.pk).exists()

    applied = io.StringIO()
    call_command(
        "apply_patient_retention",
        "--clinic",
        str(clinic.id),
        "--apply",
        stdout=applied,
    )
    assert "purged 1 patient(s)" in applied.getvalue()
    assert not Patient.all_objects.filter(pk=patient.pk).exists()


def test_management_command_rejects_a_foreign_clinic(patient_factory, clinic_factory):
    from django.core.management.base import CommandError

    clinic = clinic_factory(slug="retention-missing")
    patient_factory(clinic)
    with pytest.raises(CommandError):
        call_command(
            "apply_patient_retention",
            "--clinic",
            "00000000-0000-0000-0000-000000000000",
        )
