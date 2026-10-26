"""Invoice line math (T119)."""

from __future__ import annotations

import random

import pytest

from apps.billing.models import InvoiceLine
from apps.billing.services import invoices as invoice_service
from apps.core.money import line_subtotal_minor, line_tax_minor, line_total_minor

pytestmark = pytest.mark.django_db

INVOICES = "/api/v1/invoices/"


def _random_lines(rng: random.Random, count: int) -> list[dict]:
    lines = []
    for index in range(count):
        quantity = rng.randint(1, 5)
        unit_price = rng.randint(0, 500_000)
        discount = rng.randint(0, quantity * unit_price)
        lines.append(
            {
                "description": f"line {index}",
                "procedure_code": f"D{rng.randint(1000, 9999)}",
                "tooth": rng.randint(11, 48),
                "quantity": quantity,
                "unit_price_minor": unit_price,
                "discount_minor": discount,
                "tax_rate_bp": rng.choice([0, 500, 1200, 1800]),
            }
        )
    return lines


def test_invoice_total_equals_sum_of_rounded_lines(owner, patient_factory, invoice_factory):
    rng = random.Random(20260119)
    patient = patient_factory(owner.clinic)

    for _ in range(100):
        specs = _random_lines(rng, rng.randint(0, 6))
        invoice = invoice_factory(owner.clinic, patient, lines=specs)
        lines = list(InvoiceLine.all_objects.filter(invoice=invoice).order_by("sequence"))

        assert len(lines) == len(specs)
        for line, spec in zip(lines, specs, strict=True):
            assert line.line_total_minor == line_total_minor(
                spec["quantity"],
                spec["unit_price_minor"],
                spec["discount_minor"],
                spec["tax_rate_bp"],
            )

        assert invoice.total_minor == sum(line.line_total_minor for line in lines)
        assert invoice.subtotal_minor == sum(
            line_subtotal_minor(line.quantity, line.unit_price_minor) for line in lines
        )
        assert invoice.discount_minor == sum(line.discount_minor for line in lines)
        assert invoice.tax_minor == sum(
            line_tax_minor(
                line.quantity,
                line.unit_price_minor,
                line.discount_minor,
                line.tax_rate_bp,
            )
            for line in lines
        )
        assert invoice.balance_minor == invoice.total_minor - invoice.paid_minor
        assert invoice.number.startswith("INV-")


def test_api_recomputes_totals(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    response = owner.post(
        INVOICES,
        {
            "patient": str(patient.id),
            "total_minor": 1,
            "lines": [
                {
                    "description": "Crown",
                    "procedure_code": "D2740",
                    "quantity": 2,
                    "unit_price_minor": 150_000,
                    "discount_minor": 10_000,
                    "tax_rate_bp": 1800,
                    "line_total_minor": 1,
                }
            ],
        },
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    expected = line_total_minor(2, 150_000, 10_000, 1800)
    assert body["total_minor"] == expected
    assert body["lines"][0]["line_total_minor"] == expected
    assert body["status"] == "issued"
    assert body["number"].startswith("INV-")


def test_numbers_are_sequential_per_clinic(owner, patient_factory, invoice_factory):
    patient = patient_factory(owner.clinic)
    first = invoice_factory(owner.clinic, patient)
    second = invoice_factory(owner.clinic, patient)
    assert first.number == "INV-000001"
    assert second.number == "INV-000002"


def test_half_up_rounding_is_deterministic(owner, patient_factory, invoice_factory):
    patient = patient_factory(owner.clinic)
    # 333 * 1500 = 499500 taxable; 499500 * 1 bp / 10000 = 49.95 -> 50 half-up.
    invoice = invoice_factory(
        owner.clinic,
        patient,
        lines=[
            {
                "description": "rounding",
                "quantity": 333,
                "unit_price_minor": 1500,
                "discount_minor": 0,
                "tax_rate_bp": 1,
            }
        ],
    )
    line = InvoiceLine.all_objects.get(invoice=invoice)
    assert line.line_total_minor == 499_500 + 50
    assert invoice.tax_minor == 50


def test_from_plan_copies_accepted_items_only(
    owner, patient_factory, accepted_plan
):
    patient = patient_factory(owner.clinic)
    plan = accepted_plan(owner.clinic, patient, actor=owner.user)

    invoice = invoice_service.from_plan(plan, actor=owner.user)

    lines = list(InvoiceLine.all_objects.filter(invoice=invoice).order_by("sequence"))
    assert [line.procedure_code for line in lines] == ["D2740"]
    assert invoice.total_minor == line_total_minor(1, 250_000, 0, 1800)
    assert invoice.plan_id == plan.id


def test_from_plan_rejects_draft(owner, patient_factory, plan_factory):
    from apps.core.errors import ApiError

    patient = patient_factory(owner.clinic)
    plan = plan_factory(owner.clinic, patient, items=[])
    with pytest.raises(ApiError) as exc:
        invoice_service.from_plan(plan)
    assert exc.value.status_code == 409


def test_dentist_cannot_create_invoice(dentist, patient_factory):
    patient = patient_factory(dentist.clinic)
    response = dentist.post(INVOICES, {"patient": str(patient.id), "lines": []})
    assert response.status_code == 403
