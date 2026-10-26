"""Deterministic receipt PDFs (T119).

Rendered with ReportLab in ``invariant`` mode so the same invoice snapshot always
produces byte-identical output and therefore the same SHA-256. Nothing derives
from the wall clock; only ids, numbers, enum values and integer money are used.
Receipts contain no card data -- there is none to contain.
"""

from __future__ import annotations

import hashlib
import io
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from ..models import Invoice, Payment

_LEFT_X = 56
_TOP_Y = 800
_LINE_HEIGHT = 16


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _money(amount_minor: int, currency: str) -> str:
    sign = "-" if amount_minor < 0 else ""
    value = abs(amount_minor)
    return f"{sign}{value // 100}.{value % 100:02d} {currency}"


def receipt_snapshot(invoice: Invoice) -> dict[str, Any]:
    """A deterministic, JSON-safe projection of an invoice receipt."""

    payments = sorted(
        Payment.all_objects.filter(invoice_id=invoice.pk),
        key=lambda payment: (payment.receipt_number, str(payment.pk)),
    )
    return {
        "id": str(invoice.pk),
        "number": invoice.number,
        "patient": str(invoice.patient_id),
        "status": invoice.status,
        "currency": invoice.currency,
        "subtotal_minor": invoice.subtotal_minor,
        "discount_minor": invoice.discount_minor,
        "tax_minor": invoice.tax_minor,
        "total_minor": invoice.total_minor,
        "paid_minor": invoice.paid_minor,
        "balance_minor": invoice.balance_minor,
        "payments": [
            {
                "receipt_number": payment.receipt_number,
                "amount_minor": payment.amount_minor,
                "method": payment.method,
                "status": payment.status,
            }
            for payment in payments
        ],
    }


def render_receipt_pdf(invoice: Invoice) -> bytes:
    """Return the receipt PDF bytes (stable for the same snapshot)."""

    snap = receipt_snapshot(invoice)
    lines = [
        f"Receipt {snap['number']}",
        f"Patient {snap['patient']}",
        f"Status: {snap['status']}",
        "",
        f"Subtotal: {_money(snap['subtotal_minor'], snap['currency'])}",
        f"Discount: {_money(snap['discount_minor'], snap['currency'])}",
        f"Tax: {_money(snap['tax_minor'], snap['currency'])}",
        f"Total: {_money(snap['total_minor'], snap['currency'])}",
        f"Paid: {_money(snap['paid_minor'], snap['currency'])}",
        f"Balance: {_money(snap['balance_minor'], snap['currency'])}",
        "",
    ]
    for payment in snap["payments"]:
        lines.append(
            f"{payment['receipt_number']} "
            f"{_money(payment['amount_minor'], snap['currency'])} "
            f"({payment['method']}/{payment['status']})"
        )

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    pdf.setTitle("Receipt")
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


__all__ = ["content_hash", "receipt_snapshot", "render_receipt_pdf"]
