"""Deterministic PDF hashing (T117)."""

from __future__ import annotations

import pytest

from apps.plans.models import ConsentTemplate
from apps.plans.services import pdf as pdf_service
from apps.plans.services import signature as signature_service

pytestmark = pytest.mark.django_db


def test_plan_pdf_hash_is_stable(owner, patient_factory, plan_factory):
    patient = patient_factory(owner.clinic)
    plan = plan_factory(
        owner.clinic,
        patient,
        title="Stable plan",
        items=[
            {
                "procedure_code": "D2740",
                "quantity": 1,
                "unit_price_minor": 250_000,
                "discount_minor": 0,
                "tax_rate_bp": 1800,
            }
        ],
    )
    first = pdf_service.render_plan_pdf(plan)
    second = pdf_service.render_plan_pdf(plan)
    assert first == second
    assert pdf_service.content_hash(first) == pdf_service.content_hash(second)


def test_consent_pdf_hash_is_stable(owner, patient_factory, consent_templates):
    consent_templates(owner.clinic)
    template = ConsentTemplate.all_objects.get(clinic=owner.clinic, key="treatment_consent")
    patient = patient_factory(owner.clinic)
    record = signature_service.sign(
        patient, template, signer_name="Ada", signature_type="drawn", actor=owner.user
    )
    first = pdf_service.render_consent_pdf(record)
    second = pdf_service.render_consent_pdf(record)
    assert first == second
    assert pdf_service.content_hash(first) == pdf_service.content_hash(second)


def test_pdf_is_content_addressed(owner, patient_factory, plan_factory):
    from django.core.files.storage import default_storage

    patient = patient_factory(owner.clinic)
    plan = plan_factory(owner.clinic, patient, title="Stored", items=[])
    data = pdf_service.render_plan_pdf(plan)
    key = pdf_service.persist(data, clinic_id=owner.clinic.id)
    assert key.endswith(f"{pdf_service.content_hash(data)}.pdf")
    assert default_storage.exists(key)
