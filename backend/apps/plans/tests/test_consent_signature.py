"""Consent signing and template versioning (T117)."""

from __future__ import annotations

import pytest

from apps.plans.models import ConsentRecord, ConsentTemplate
from apps.plans.services import consent as consent_service
from apps.plans.services import signature as signature_service

pytestmark = pytest.mark.django_db

CONSENT_TEMPLATES = "/api/v1/consent-templates/"
CONSENT_RECORDS = "/api/v1/consent-records/"


def test_signature_binds_snapshot_fields(owner, patient_factory, consent_templates):
    consent_templates(owner.clinic)
    template = ConsentTemplate.all_objects.get(
        clinic=owner.clinic, key="treatment_consent"
    )
    patient = patient_factory(owner.clinic)

    response = owner.post(
        CONSENT_RECORDS,
        {
            "patient": str(patient.id),
            "template": str(template.id),
            "signer_name": "Ada Lovelace",
            "relationship": "self",
            "signature_type": "typed",
        },
        HTTP_USER_AGENT="pytest-agent/1.0",
    )
    assert response.status_code == 201, response.json()
    body = response.json()

    assert body["template_version"] == template.version
    assert body["signer_name"] == "Ada Lovelace"
    assert body["relationship"] == "self"
    assert body["signature_type"] == "typed"
    assert body["status"] == "signed"
    assert body["signed_at"]

    record = ConsentRecord.all_objects.get(pk=body["id"])
    assert len(record.ip_hash) == 64  # salted SHA-256, never the raw IP
    assert record.user_agent == "pytest-agent/1.0"

    # drawn/typed are explicitly NOT claimed as a legally compliant e-signature.
    assert body["is_legally_compliant"] is False
    assert "not a legally compliant" in body["legal_notice"]


def test_external_esign_is_marked_compliant(owner, patient_factory, consent_templates):
    consent_templates(owner.clinic)
    template = ConsentTemplate.all_objects.get(clinic=owner.clinic, key="treatment_consent")
    patient = patient_factory(owner.clinic)
    record = signature_service.sign(
        patient,
        template,
        signer_name="Ada",
        signature_type="external_esign",
        actor=owner.user,
    )
    assert record.status == ConsentRecord.Status.SIGNED
    assert signature_service.legal_notice("external_esign") == ""


def test_template_edit_bumps_version_and_keeps_old_consent(
    owner, patient_factory, consent_templates
):
    consent_templates(owner.clinic)
    template = ConsentTemplate.all_objects.get(clinic=owner.clinic, key="treatment_consent")
    patient = patient_factory(owner.clinic)

    record = signature_service.sign(
        patient, template, signer_name="Ada", signature_type="typed", actor=owner.user
    )
    assert record.template_version == 1

    patched = owner.patch(
        f"{CONSENT_TEMPLATES}{template.id}/",
        {"body": "A new wording."},
    )
    assert patched.status_code == 200, patched.json()
    assert patched.json()["version"] == 2

    template.refresh_from_db()
    assert template.version == 2
    record.refresh_from_db()
    assert record.template_version == 1  # the signed snapshot never changes


def test_old_consent_renders_its_own_version(owner, patient_factory, consent_templates):
    from apps.plans.services import pdf as pdf_service

    consent_templates(owner.clinic)
    template = ConsentTemplate.all_objects.get(clinic=owner.clinic, key="treatment_consent")
    patient = patient_factory(owner.clinic)
    record_v1 = signature_service.sign(
        patient, template, signer_name="Ada", signature_type="typed", actor=owner.user
    )

    consent_service.edit_template(template, fields={"body": "Changed."}, actor=owner.user)
    record_v2 = signature_service.sign(
        patient, template, signer_name="Ada", signature_type="typed", actor=owner.user
    )

    assert record_v1.template_version == 1
    assert record_v2.template_version == 2
    assert pdf_service.consent_snapshot(record_v1)["template_version"] == 1
    assert pdf_service.consent_snapshot(record_v2)["template_version"] == 2
    # The two snapshots render differently; the old consent keeps its version.
    assert pdf_service.render_consent_pdf(record_v1) != pdf_service.render_consent_pdf(record_v2)
    record_v1.refresh_from_db()
    assert record_v1.template_version == 1


def test_consent_pdf_endpoint_is_stable(owner, patient_factory, consent_templates):
    consent_templates(owner.clinic)
    template = ConsentTemplate.all_objects.get(clinic=owner.clinic, key="treatment_consent")
    patient = patient_factory(owner.clinic)
    record = signature_service.sign(
        patient, template, signer_name="Ada", signature_type="typed", actor=owner.user
    )

    first = owner.get(f"/api/v1/consents/{record.id}/pdf/")
    second = owner.get(f"/api/v1/consents/{record.id}/pdf/")
    assert first.status_code == 200
    assert first["Content-Type"] == "application/pdf"
    assert first["X-Document-Sha256"] == second["X-Document-Sha256"]


def test_consent_template_create_validates_unique_key(owner):
    created = owner.post(
        CONSENT_TEMPLATES,
        {
            "key": "ortho_consent",
            "name": "Ortho consent",
            "purpose": "orthodontics",
            "body": "I consent.",
        },
    )
    assert created.status_code == 201, created.json()
    assert created.json()["version"] == 1

    duplicate = owner.post(
        CONSENT_TEMPLATES,
        {
            "key": "ortho_consent",
            "name": "Duplicate",
            "purpose": "orthodontics",
            "body": "I consent.",
        },
    )
    assert duplicate.status_code == 400


def test_assistant_cannot_manage_consent_templates(assistant):
    response = assistant.get(CONSENT_TEMPLATES)
    assert response.status_code == 403


def test_external_esign_adapter_is_disabled_by_default(settings):
    from apps.plans.adapters.esign import AdapterNotConfigured, get_esign_adapter

    settings.ESIGN_PROVIDER = ""
    with pytest.raises(AdapterNotConfigured):
        get_esign_adapter()
