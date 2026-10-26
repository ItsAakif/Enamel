"""Signed receipt-download tokens for the patient portal (T122).

Receipts are served through a short-lived signed token rather than a raw media
URL, mirroring ``apps.plans.services.documents``: the portal invoice list hands
out ``receipt_url`` values that already carry a token, and the download view
re-checks the portal identity *and* verifies the token. A leaked URL therefore
cannot be used from another patient session, and the token cannot be replayed
against a different invoice.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core import signing

RECEIPT_SALT = "portal.invoice.receipt"
DEFAULT_TTL_SECONDS = 300


def ttl_seconds() -> int:
    return int(getattr(settings, "DOCUMENT_DOWNLOAD_TTL_SECONDS", DEFAULT_TTL_SECONDS))


def issue_receipt_token(invoice: Any) -> str:
    """Return a signed, short-lived token bound to one invoice."""

    return signing.dumps(
        {
            "invoice": str(invoice.pk),
            "clinic": str(invoice.clinic_id),
            "patient": str(invoice.patient_id),
        },
        salt=RECEIPT_SALT,
        compress=True,
    )


def verify_receipt_token(token: str, *, invoice: Any) -> bool:
    """True when ``token`` is valid, unexpired and bound to ``invoice``."""

    if not token:
        return False
    try:
        payload = signing.loads(token, salt=RECEIPT_SALT, max_age=ttl_seconds())
    except signing.SignatureExpired:
        return False
    except signing.BadSignature:
        return False
    except Exception:  # pragma: no cover - defensive: any malformed token
        return False
    return (
        str(payload.get("invoice")) == str(invoice.pk)
        and str(payload.get("clinic")) == str(invoice.clinic_id)
        and str(payload.get("patient")) == str(invoice.patient_id)
    )


def receipt_path(invoice: Any) -> str:
    """The API path for a receipt download (without the token)."""

    return f"/api/v1/portal/invoices/{invoice.pk}/receipt/"


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "RECEIPT_SALT",
    "issue_receipt_token",
    "receipt_path",
    "ttl_seconds",
    "verify_receipt_token",
]
