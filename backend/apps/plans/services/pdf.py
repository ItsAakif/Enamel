"""Deterministic PDF generation (T117).

PDFs are rendered with ReportLab in ``invariant`` mode, which pins the document
id and the creation/modification timestamps, so the *same input snapshot* always
produces byte-identical output and therefore the same SHA-256. Nothing in the
document is derived from the wall clock: only ids, sequence numbers, enum values
and integer money.

Generated reports are content-addressed: :func:`persist` writes the bytes under
``clinic/<id>/reports/<sha256>.pdf`` and returns the storage key, so the same
snapshot is never stored twice.
"""

from __future__ import annotations

import hashlib
import io
from typing import Any

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from ..models import ConsentRecord, PlanItem, TreatmentPlan

#: Right-hand column where money is drawn.
_AMOUNT_X = 480
_LEFT_X = 56
_TOP_Y = 800
_LINE_HEIGHT = 16


def content_hash(data: bytes) -> str:
    """Return the hex SHA-256 of ``data``."""

    return hashlib.sha256(data).hexdigest()


def _draw(lines: list[str], *, title: str) -> bytes:
    """Render ``lines`` into an invariant ReportLab canvas."""

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    pdf.setTitle(title)
    pdf.setAuthor("Enamel")
    pdf.setCreator("Enamel")
    pdf.setProducer("Enamel")
    y = _TOP_Y
    for line in lines:
        pdf.drawString(_LEFT_X, y, line)
        y -= _LINE_HEIGHT
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _money(amount_minor: int, currency: str) -> str:
    """Render integer minor units as ``1234.56 INR`` without any float math."""

    sign = "-" if amount_minor < 0 else ""
    value = abs(amount_minor)
    return f"{sign}{value // 100}.{value % 100:02d} {currency}"


def plan_snapshot(plan: TreatmentPlan) -> dict[str, Any]:
    """A deterministic, JSON-safe projection of a plan for rendering/auditing."""

    items = sorted(
        PlanItem.all_objects.filter(plan_id=plan.pk),
        key=lambda item: (item.sequence, str(item.pk)),
    )
    return {
        "id": str(plan.pk),
        "patient": str(plan.patient_id),
        "title": plan.title,
        "status": plan.status,
        "currency": plan.currency,
        "subtotal_minor": plan.subtotal_minor,
        "discount_minor": plan.discount_minor,
        "tax_minor": plan.tax_minor,
        "total_minor": plan.total_minor,
        "items": [
            {
                "sequence": item.sequence,
                "procedure_code": item.procedure_code,
                "description": item.description,
                "tooth": item.tooth,
                "quantity": item.quantity,
                "unit_price_minor": item.unit_price_minor,
                "discount_minor": item.discount_minor,
                "tax_rate_bp": item.tax_rate_bp,
                "line_total_minor": item.line_total_minor,
                "status": item.status,
            }
            for item in items
        ],
    }


def consent_snapshot(record: ConsentRecord) -> dict[str, Any]:
    """A deterministic projection of a signed consent record."""

    return {
        "id": str(record.pk),
        "patient": str(record.patient_id),
        "template_key": record.template.key,
        "template_version": record.template_version,
        "signer_name": record.signer_name,
        "relationship": record.relationship,
        "signature_type": record.signature_type,
        "document_hash": record.document_hash,
        "status": record.status,
    }


def render_plan_pdf(plan: TreatmentPlan) -> bytes:
    """Return the plan PDF bytes (stable for the same snapshot)."""

    snap = plan_snapshot(plan)
    lines = [
        f"Treatment plan {snap['id']}",
        f"Patient {snap['patient']}",
        f"Title: {snap['title']}",
        f"Status: {snap['status']}",
        "",
    ]
    for item in snap["items"]:
        lines.append(
            f"{item['sequence']}. {item['procedure_code']} "
            f"x{item['quantity']} @ "
            f"{_money(item['unit_price_minor'], snap['currency'])} "
            f"= {_money(item['line_total_minor'], snap['currency'])}"
        )
    lines.extend(
        [
            "",
            f"Subtotal: {_money(snap['subtotal_minor'], snap['currency'])}",
            f"Discount: {_money(snap['discount_minor'], snap['currency'])}",
            f"Tax: {_money(snap['tax_minor'], snap['currency'])}",
            f"Total: {_money(snap['total_minor'], snap['currency'])}",
        ]
    )
    return _draw(lines, title="Treatment plan")


def render_consent_pdf(record: ConsentRecord) -> bytes:
    """Return the signed-consent PDF bytes (stable for the same snapshot)."""

    snap = consent_snapshot(record)
    lines = [
        f"Consent record {snap['id']}",
        f"Template: {snap['template_key']} v{snap['template_version']}",
        f"Patient: {snap['patient']}",
        f"Signer: {snap['signer_name']}",
        f"Relationship: {snap['relationship']}",
        f"Signature type: {snap['signature_type']}",
        f"Document SHA-256: {snap['document_hash'] or 'n/a'}",
        f"Status: {snap['status']}",
    ]
    return _draw(lines, title="Consent record")


def persist(data: bytes, *, clinic_id: Any, prefix: str = "reports") -> str:
    """Store ``data`` content-addressed and return its storage key."""

    digest = content_hash(data)
    key = f"clinic/{clinic_id}/{prefix}/{digest}.pdf"
    if not default_storage.exists(key):
        default_storage.save(key, ContentFile(data))
    return key


__all__ = [
    "consent_snapshot",
    "content_hash",
    "persist",
    "plan_snapshot",
    "render_consent_pdf",
    "render_plan_pdf",
]
