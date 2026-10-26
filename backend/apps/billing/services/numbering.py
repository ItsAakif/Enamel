"""Per-clinic invoice and receipt numbering (T119).

``Invoice.number`` is ``INV-`` plus a zero-padded 6 digit sequence that restarts
for every clinic. The sequence lives in ``billing_invoicecounter`` and is handed
out under a ``SELECT ... FOR UPDATE`` row lock, so two concurrent creates in one
clinic can never receive the same number. Receipt numbers are derived from the
invoice number plus the sequence of successful payments on that invoice, which
is stable because payments are recorded under the invoice's row lock.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from ..models import Invoice, InvoiceCounter

INVOICE_NUMBER_PREFIX = "INV-"
INVOICE_NUMBER_WIDTH = 6
RECEIPT_NUMBER_PREFIX = "RCPT-"


def format_invoice_number(value: int) -> str:
    """Return ``1`` -> ``INV-000001``."""

    if value < 1:
        raise ValueError("Invoice numbers start at 1.")
    return f"{INVOICE_NUMBER_PREFIX}{value:0{INVOICE_NUMBER_WIDTH}d}"


def _ensure_counter(clinic: Any) -> None:
    InvoiceCounter.objects.get_or_create(clinic=clinic)


def reserve_invoice_number(clinic: Any) -> int:
    """Reserve and return the next integer for ``clinic`` (row locked)."""

    _ensure_counter(clinic)
    with transaction.atomic():
        counter = InvoiceCounter.objects.select_for_update().get(clinic=clinic)
        reserved = counter.next_number
        counter.next_number = reserved + 1
        counter.save(update_fields=["next_number", "updated_at"])
    return reserved


def allocate_invoice_number(clinic: Any) -> str:
    return format_invoice_number(reserve_invoice_number(clinic))


def format_receipt_number(invoice: Invoice, sequence: int) -> str:
    """Return the receipt number for the ``sequence``-th payment on ``invoice``."""

    return f"{RECEIPT_NUMBER_PREFIX}{invoice.number}-{sequence:02d}"


__all__ = [
    "INVOICE_NUMBER_PREFIX",
    "INVOICE_NUMBER_WIDTH",
    "RECEIPT_NUMBER_PREFIX",
    "allocate_invoice_number",
    "format_invoice_number",
    "format_receipt_number",
    "reserve_invoice_number",
]
