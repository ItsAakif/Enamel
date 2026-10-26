"""Invoice creation and line math (T119).

The server recomputes every line total and every invoice total from the line
inputs with integer minor units and half-up rounding (``apps.core.money``); a
client-supplied total is never trusted. ``from_plan`` copies the *accepted* items
of an accepted treatment plan only, so a declined procedure can never be billed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.money import line_subtotal_minor, line_tax_minor, line_total_minor
from apps.core.tenancy import with_clinic
from apps.plans.models import PlanItem, TreatmentPlan

from ..models import Invoice, InvoiceLine
from .numbering import allocate_invoice_number

SNAPSHOT_FIELDS = (
    "number",
    "status",
    "currency",
    "subtotal_minor",
    "discount_minor",
    "tax_minor",
    "total_minor",
    "paid_minor",
    "balance_minor",
    "version",
)

#: Fields copied from a line spec onto an ``InvoiceLine``.
LINE_FIELDS = (
    "description",
    "procedure_code",
    "tooth",
    "quantity",
    "unit_price_minor",
    "discount_minor",
    "tax_rate_bp",
)


def snapshot(invoice: Invoice) -> dict[str, Any]:
    """Audit projection for an invoice (enums and money only)."""

    return {name: getattr(invoice, name) for name in SNAPSHOT_FIELDS}


def lines_for(invoice: Invoice) -> list[InvoiceLine]:
    return list(
        InvoiceLine.all_objects.filter(invoice_id=invoice.pk).order_by("sequence", "id")
    )


def compute_totals(lines: list[InvoiceLine]) -> dict[str, int]:
    """Return invoice totals derived from already-stored line inputs."""

    subtotal = sum(line_subtotal_minor(i.quantity, i.unit_price_minor) for i in lines)
    discount = sum(i.discount_minor for i in lines)
    tax = sum(
        line_tax_minor(i.quantity, i.unit_price_minor, i.discount_minor, i.tax_rate_bp)
        for i in lines
    )
    total = sum(
        line_total_minor(i.quantity, i.unit_price_minor, i.discount_minor, i.tax_rate_bp)
        for i in lines
    )
    return {
        "subtotal_minor": subtotal,
        "discount_minor": discount,
        "tax_minor": tax,
        "total_minor": total,
    }


def recompute(invoice: Invoice) -> Invoice:
    """Recompute line totals and invoice totals/balance in place."""

    lines = lines_for(invoice)
    for line in lines:
        total = line_total_minor(
            line.quantity, line.unit_price_minor, line.discount_minor, line.tax_rate_bp
        )
        if line.line_total_minor != total:
            line.line_total_minor = total
            line.save(update_fields=["line_total_minor", "updated_at"])

    totals = compute_totals(lines)
    changed = False
    for field, value in totals.items():
        if getattr(invoice, field) != value:
            setattr(invoice, field, value)
            changed = True
    balance = invoice.total_minor - invoice.paid_minor
    if invoice.balance_minor != balance:
        invoice.balance_minor = balance
        changed = True
    if changed:
        invoice.save(
            update_fields=[*totals.keys(), "balance_minor", "updated_at"]
        )
    return invoice


def _create_line(invoice: Invoice, spec: dict[str, Any], sequence: int) -> InvoiceLine:
    return InvoiceLine.all_objects.create(
        clinic_id=invoice.clinic_id,
        invoice=invoice,
        sequence=sequence,
        description=spec.get("description", "") or "",
        procedure_code=spec.get("procedure_code", "") or "",
        tooth=spec.get("tooth"),
        quantity=spec.get("quantity", 1),
        unit_price_minor=spec.get("unit_price_minor", 0),
        discount_minor=spec.get("discount_minor", 0),
        tax_rate_bp=spec.get("tax_rate_bp", 0),
        line_total_minor=0,
    )


def create_invoice(
    clinic: Any,
    *,
    patient: Any,
    lines: list[dict[str, Any]],
    currency: str | None = None,
    plan: TreatmentPlan | None = None,
    notes: str = "",
    due_at: datetime | None = None,
    status: str = Invoice.Status.ISSUED,
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> Invoice:
    """Create an issued invoice with its lines and recomputed totals."""

    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(clinic):
            number = allocate_invoice_number(clinic)
            invoice = Invoice.all_objects.create(
                clinic_id=clinic.id,
                patient=patient,
                plan=plan,
                number=number,
                status=status,
                currency=currency or clinic.currency,
                notes=notes,
                due_at=due_at,
                issued_at=moment,
            )
            for index, spec in enumerate(lines):
                _create_line(invoice, spec, index)
            recompute(invoice)
            record_audit(
                "invoice.created",
                entity=invoice,
                after=snapshot(invoice),
                actor=actor,
                request=request,
            )
    return invoice


def lines_from_plan_items(plan: TreatmentPlan) -> list[dict[str, Any]]:
    """Return line specs for the *accepted* items of ``plan`` only."""

    items = PlanItem.all_objects.filter(
        plan_id=plan.pk, status=PlanItem.Status.ACCEPTED
    ).order_by("sequence", "id")
    return [
        {
            "description": item.description or item.procedure_code,
            "procedure_code": item.procedure_code,
            "tooth": item.tooth,
            "quantity": item.quantity,
            "unit_price_minor": item.unit_price_minor,
            "discount_minor": item.discount_minor,
            "tax_rate_bp": item.tax_rate_bp,
        }
        for item in items
    ]


def from_plan(
    plan: TreatmentPlan,
    *,
    actor: Any = None,
    request: Any = None,
    due_at: datetime | None = None,
) -> Invoice:
    """Create an invoice from the accepted items of ``plan``."""

    with transaction.atomic():
        with with_clinic(plan.clinic):
            # Re-read under the clinic scope (and a row lock) so the status check
            # is against committed state, not a possibly stale caller object.
            current = TreatmentPlan.all_objects.select_for_update().get(pk=plan.pk)
            if current.status not in (
                TreatmentPlan.Status.ACCEPTED,
                TreatmentPlan.Status.COMPLETED,
            ):
                raise ApiError(
                    "invalid_transition",
                    "An invoice can only be raised from an accepted plan.",
                    status_code=409,
                    details={"status": current.status},
                )
            lines = lines_from_plan_items(current)
            if not lines:
                raise ApiError(
                    "validation_error",
                    "The plan has no accepted items to invoice.",
                    status_code=400,
                    field_errors={"plan": ["no accepted items"]},
                )
            return create_invoice(
                current.clinic,
                patient=current.patient,
                lines=lines,
                currency=current.currency,
                plan=current,
                due_at=due_at,
                actor=actor,
                request=request,
            )


__all__ = [
    "LINE_FIELDS",
    "SNAPSHOT_FIELDS",
    "compute_totals",
    "create_invoice",
    "from_plan",
    "lines_for",
    "lines_from_plan_items",
    "recompute",
    "snapshot",
]
