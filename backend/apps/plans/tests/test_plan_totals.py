"""Plan pricing invariants (T117)."""

from __future__ import annotations

import random

import pytest

from apps.core.money import line_tax_minor, line_total_minor
from apps.plans.models import PlanItem

pytestmark = pytest.mark.django_db

PLANS = "/api/v1/treatment-plans/"


def _random_items(rng: random.Random, count: int) -> list[dict]:
    items = []
    for index in range(count):
        quantity = rng.randint(1, 5)
        unit_price = rng.randint(0, 500_000)
        discount = rng.randint(0, quantity * unit_price)
        items.append(
            {
                "procedure_code": f"D{rng.randint(1000, 9999)}",
                "description": f"procedure {index}",
                "tooth": rng.randint(11, 48),
                "quantity": quantity,
                "unit_price_minor": unit_price,
                "discount_minor": discount,
                "tax_rate_bp": rng.choice([0, 500, 1200, 1800]),
            }
        )
    return items


def test_plan_total_equals_sum_of_rounded_item_totals(owner, patient_factory, plan_factory):
    rng = random.Random(20260101)
    patient = patient_factory(owner.clinic)

    for _ in range(100):
        specs = _random_items(rng, rng.randint(0, 6))
        plan = plan_factory(owner.clinic, patient, title="Property plan", items=specs)
        items = list(PlanItem.all_objects.filter(plan=plan).order_by("sequence"))

        assert len(items) == len(specs)
        for item, spec in zip(items, specs, strict=True):
            expected = line_total_minor(
                spec["quantity"],
                spec["unit_price_minor"],
                spec["discount_minor"],
                spec["tax_rate_bp"],
            )
            assert item.line_total_minor == expected

        assert plan.total_minor == sum(item.line_total_minor for item in items)
        assert plan.subtotal_minor == sum(
            item.quantity * item.unit_price_minor for item in items
        )
        assert plan.discount_minor == sum(item.discount_minor for item in items)
        assert plan.tax_minor == sum(
            line_tax_minor(
                item.quantity,
                item.unit_price_minor,
                item.discount_minor,
                item.tax_rate_bp,
            )
            for item in items
        )
        assert plan.total_minor == plan.subtotal_minor - plan.discount_minor + plan.tax_minor


def test_client_supplied_totals_are_ignored(owner, patient_factory, plan_factory):
    patient = patient_factory(owner.clinic)
    plan = plan_factory(
        owner.clinic,
        patient,
        items=[
            {
                "procedure_code": "D1110",
                "quantity": 2,
                "unit_price_minor": 100_00,
                "discount_minor": 0,
                "tax_rate_bp": 1800,
                # A hostile client tries to pin the line total; it is ignored.
                "line_total_minor": 1,
            }
        ],
    )
    item = PlanItem.all_objects.get(plan=plan)
    assert item.line_total_minor == line_total_minor(2, 100_00, 0, 1800)
    assert plan.total_minor == item.line_total_minor


def test_api_recomputes_totals_on_create(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    response = owner.post(
        PLANS,
        {
            "patient": str(patient.id),
            "title": "API plan",
            "total_minor": 999_999,
            "items": [
                {
                    "procedure_code": "D2740",
                    "quantity": 1,
                    "unit_price_minor": 250_000,
                    "discount_minor": 25_000,
                    "tax_rate_bp": 1800,
                    "line_total_minor": 1,
                }
            ],
        },
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    expected = line_total_minor(1, 250_000, 25_000, 1800)
    assert body["total_minor"] == expected
    assert body["items"][0]["line_total_minor"] == expected
    assert body["subtotal_minor"] == 250_000
    assert body["tax_minor"] == line_tax_minor(1, 250_000, 25_000, 1800)
