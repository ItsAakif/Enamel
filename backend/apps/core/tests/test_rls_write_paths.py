"""The application write paths must work under the runtime role (T138).

The role switch exposed three application paths that wrote tenant rows
outside a clinic scope. Under the runtime role (``enamel_app``) the database
refuses those writes, so they are production defects, not test artefacts:

* the RAG ingest (``apps.ai.rag.ingest``) wrote its document and its chunks
  without a clinic context;
* the no-show model registry (``ml.no_show.registry``) wrote a clinic-scoped
  model without a clinic context;
* the evaluation world builder (``apps.ai.evals.runner``) wrote locations,
  providers, appointment types and availability rules without a clinic
  context.

Each test below runs the write **as the runtime role** and asserts that it
succeeds and that the row is scoped to the right tenant. The global
no-show catalogue row is the documented exception: it is a platform row and it
is written on the platform path (the maintenance role or the owner), never by
the runtime role.

The module never skips: it fails loudly when the environment cannot run the
proofs.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from django.db import DatabaseError, connection, transaction

from apps.core.rls import rls_context
from apps.core.tenancy import with_clinic
from apps.core.tests.rls_owner_connection import owner_connection  # noqa: F401  (the boundary proofs need a role switch)
from apps.patients.models import Patient

pytestmark = [pytest.mark.django_db, pytest.mark.rls]


APP_ROLE = "enamel_app"
MAINT_ROLE = "enamel_maint"


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


def make_patient(clinic, number: str, first: str) -> Patient:
    return Patient.all_objects.create(
        clinic=clinic,
        number=number,
        first_name=first,
        last_name="WritePath",
    )


def test_the_rag_ingest_writes_inside_the_clinic_scope(clinic_factory):
    """The knowledge document and its chunks are written in the clinic scope."""

    from apps.ai.models import KnowledgeChunk, KnowledgeDocument
    from apps.ai.rag.ingest import ingest_document

    clinic = clinic_factory(slug="rls-write-rag")
    other = clinic_factory(slug="rls-write-rag-other")

    with as_role(APP_ROLE):
        result = ingest_document(
            clinic,
            title="Playbook",
            text="A line of clinic policy that has to be indexed.",
            kind="policy",
        )
        assert result.created is True
        document_id = result.document.id

        # The rows exist and they belong to this clinic.
        with with_clinic(clinic):
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT clinic_id FROM ai_knowledgedocument WHERE id = %s", [document_id]
                )
                row = cursor.fetchone()
            assert row is not None
            assert str(row[0]) == str(clinic.id)

        # And they are invisible from another clinic's context.
        with rls_context(other.id):
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM ai_knowledgechunk WHERE document_id = %s",
                    [document_id],
                )
                assert cursor.fetchone()[0] == 0

    with with_clinic(clinic):
        assert KnowledgeChunk.all_objects.filter(document_id=document_id).count() >= 1
        assert KnowledgeDocument.all_objects.filter(id=document_id).count() == 1


def test_the_no_show_registry_writes_a_clinic_model_inside_the_clinic_scope(
    clinic_factory, tmp_path
):
    """A clinic-scoped model row is written in the clinic scope."""

    from apps.ml.models import NoShowModel, TrainingRun
    from ml.no_show.registry import register_model

    clinic = clinic_factory(slug="rls-write-ml")
    other = clinic_factory(slug="rls-write-ml-other")
    metrics = _metrics()

    with as_role(APP_ROLE):
        model, run = register_model(
            metrics,
            artifact_path=str(tmp_path / "model.joblib"),
            artifact_hash="probe-artifact-hash",
            gate_passed=True,
            clinic=clinic,
        )

    assert model.clinic_id == clinic.id
    assert run.clinic_id == clinic.id
    assert NoShowModel.objects.filter(pk=model.pk).count() == 1
    assert TrainingRun.objects.filter(pk=run.pk).count() == 1

    # The row is invisible from another clinic's context.
    with as_role(APP_ROLE), rls_context(other.id):
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM ml_noshowmodel WHERE id = %s", [model.pk])
            assert cursor.fetchone()[0] == 0


def test_the_global_catalogue_row_is_a_platform_write(clinic_factory, tmp_path):
    """The global no-show row is refused by the runtime role.

    It is a platform row (``clinic_id IS NULL``): ADR-013 puts it on the
    platform path, so the runtime role is refused by the database and the
    maintenance role (which holds the bypass) may write it.
    """

    from apps.ml.models import NoShowModel
    from ml.no_show.registry import register_model

    metrics = _metrics()
    with as_role(APP_ROLE):
        with pytest.raises(DatabaseError), transaction.atomic():
            register_model(
                metrics,
                artifact_path=str(tmp_path / "global.joblib"),
                artifact_hash="probe-artifact-hash",
                gate_passed=True,
                clinic=None,
            )

    assert NoShowModel.objects.filter(clinic__isnull=True).count() == 0

    # The platform path (the maintenance role, which holds the bypass) may
    # write it.
    with as_role(MAINT_ROLE):
        from apps.core.rls import bypass_rls

        with bypass_rls():
            model, _run = register_model(
                metrics,
                artifact_path=str(tmp_path / "global.joblib"),
                artifact_hash="probe-artifact-hash",
                gate_passed=True,
                clinic=None,
            )
    assert model.clinic_id is None


def test_the_location_service_writes_inside_the_clinic_scope(clinic_factory):
    """The default location of a clinic is written in the clinic scope."""

    from apps.clinics.models import Location
    from apps.clinics.services.members import create_default_location

    clinic = clinic_factory(slug="rls-write-location")
    other = clinic_factory(slug="rls-write-location-other")

    with as_role(APP_ROLE):
        location = create_default_location(clinic)

    assert location.clinic_id == clinic.id
    assert Location.all_objects.filter(pk=location.pk).count() == 1

    with as_role(APP_ROLE), rls_context(other.id):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM clinics_location WHERE id = %s", [location.pk]
            )
            assert cursor.fetchone()[0] == 0


def test_the_eval_world_is_built_inside_the_clinic_scope(clinic_factory):
    """The evaluation world builder writes its tenant rows in the scope."""

    from apps.ai.evals import runner

    clinic = clinic_factory(slug="rls-write-eval")
    other = clinic_factory(slug="rls-write-eval-other")

    with as_role(APP_ROLE):
        location = runner._location(clinic, "Eval room")
        provider = runner._provider(clinic, _membership(clinic), "Dr Eval")
        appointment_type = runner._appointment_type(clinic)
        runner._working_rules(clinic, location)

    assert location.clinic_id == clinic.id
    assert provider.clinic_id == clinic.id
    assert appointment_type.clinic_id == clinic.id

    with as_role(APP_ROLE), rls_context(other.id):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM clinics_location WHERE id = %s", [location.pk]
            )
            assert cursor.fetchone()[0] == 0


def _membership(clinic):
    from django.contrib.auth import get_user_model

    from apps.clinics.services.members import create_membership

    user = get_user_model().objects.create_user(
        email=f"rls-write-eval-{clinic.slug}@bright.test",
        password="Str0ngPass!2026",
    )
    return create_membership(clinic, user, role="owner")


def _metrics() -> dict:
    """A minimal metrics document for the registry."""

    return {
        "version": "v1-probe",
        "algorithm": "logistic",
        "feature_names": ["lead_time"],
        "dataset": {"hash": "dataset-hash"},
        "threshold": 0.5,
        "metrics_hash": "metrics-hash",
        "seed": 7,
        "sklearn_version": "1.9.1",
        "trained_at": "2026-10-20T00:00:00Z",
    }


__all__ = [
    "test_the_eval_world_is_built_inside_the_clinic_scope",
    "test_the_global_catalogue_row_is_a_platform_write",
    "test_the_location_service_writes_inside_the_clinic_scope",
    "test_the_no_show_registry_writes_a_clinic_model_inside_the_clinic_scope",
    "test_the_rag_ingest_writes_inside_the_clinic_scope",
]
