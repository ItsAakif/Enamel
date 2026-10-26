"""Consent signing (T117).

A signature is a *snapshot binding*: it records exactly what was agreed to and
under which conditions. :func:`sign` writes a :class:`~apps.plans.models.ConsentRecord`
that binds the template version, the signer and their relationship, the
timestamp, a salted hash of the request IP, the user agent, the signature type
and the SHA-256 of the signed artifact.

Signature types ``drawn`` and ``typed`` are convenience capture only. They are
explicitly **not** claimed to be a legally compliant e-signature, and the API
payload says so (:func:`legal_notice`). Only ``external_esign`` (through the
isolated adapter) may carry that claim.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.audit import hash_ip
from apps.core.audit import record as record_audit
from apps.core.tenancy import with_clinic

from ..models import ConsentRecord, ConsentTemplate, Document

LEGAL_NOTICE = (
    "This signature was captured as a simple electronic mark. It is not a "
    "legally compliant electronic signature and carries no cryptographic "
    "signer authentication."
)

LEGAL_NOTICE_TYPES = frozenset(
    {ConsentRecord.SignatureType.DRAWN, ConsentRecord.SignatureType.TYPED}
)

SNAPSHOT_FIELDS = (
    "template_id",
    "template_version",
    "signature_type",
    "status",
)


def legal_notice(signature_type: str) -> str:
    """Return the disclaimer for signature types that are not e-signatures."""

    if signature_type in LEGAL_NOTICE_TYPES:
        return LEGAL_NOTICE
    return ""


def _client_ip(request: Any) -> str:
    meta = getattr(request, "META", None)
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("REMOTE_ADDR") or "")


def _user_agent(request: Any) -> str:
    meta = getattr(request, "META", None)
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("HTTP_USER_AGENT") or "")[:255]


def snapshot(record: ConsentRecord) -> dict[str, Any]:
    """Audit projection for a consent record (no signer name, no user agent)."""

    return {name: getattr(record, name) for name in SNAPSHOT_FIELDS}


def sign(
    patient: Any,
    template: ConsentTemplate,
    *,
    signer_name: str,
    relationship: str = "",
    signature_type: str = ConsentRecord.SignatureType.TYPED,
    document: Document | None = None,
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> ConsentRecord:
    """Create a signed consent record bound to ``template.version``."""

    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(patient.clinic):
            record = ConsentRecord.all_objects.create(
                clinic_id=patient.clinic_id,
                patient=patient,
                template=template,
                template_version=template.version,
                signer_name=signer_name,
                relationship=relationship,
                signature_type=signature_type,
                signature_document=document,
                signed_at=moment,
                ip_hash=hash_ip(_client_ip(request)),
                user_agent=_user_agent(request),
                document_hash=document.sha256 if document is not None else "",
                status=ConsentRecord.Status.SIGNED,
            )
            record_audit(
                "consent.signed",
                entity=record,
                after=snapshot(record),
                actor=actor,
                request=request,
            )
    return record


__all__ = [
    "LEGAL_NOTICE",
    "LEGAL_NOTICE_TYPES",
    "SNAPSHOT_FIELDS",
    "legal_notice",
    "sign",
    "snapshot",
]
