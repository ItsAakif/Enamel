"""Consent registry (T114).

A grant is idempotent: re-granting refreshes the evidence on the existing
*granted* row instead of creating a second one. A revocation is immediate and
happens in a single transaction that

1. flips the consent row to ``revoked``,
2. inserts suppression rows for every channel the patient has an address for
   (cross-channel), and
3. skips any queued messages for that patient on every channel.

Because all three writes share one transaction, a reader can never observe a
revoked consent while a queued message is still about to be sent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.audit import record as record_audit
from apps.core.tenancy import with_clinic

from ..models import Consent, Message, Suppression
from . import suppression

SNAPSHOT_FIELDS = ("channel", "purpose", "status", "source", "wording_version")


def snapshot(consent: Consent) -> dict[str, Any]:
    """Audit projection for a consent row (enums only, no evidence payload)."""

    return {name: getattr(consent, name) for name in SNAPSHOT_FIELDS}


def is_granted(patient: Any, channel: str, purpose: str) -> bool:
    """True when an active *granted* consent row exists for the tuple."""

    return Consent.all_objects.filter(
        clinic_id=patient.clinic_id,
        patient_id=patient.pk,
        channel=channel,
        purpose=purpose,
        status=Consent.Status.GRANTED,
    ).exists()


def grant(
    patient: Any,
    channel: str,
    purpose: str,
    *,
    source: str = "",
    wording_version: str = "",
    evidence: dict[str, Any] | None = None,
    ip_hash: str = "",
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> Consent:
    """Grant (or refresh) consent for ``patient`` x ``channel`` x ``purpose``."""

    clinic = patient.clinic
    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(clinic):
            existing = Consent.all_objects.filter(
                clinic=clinic,
                patient_id=patient.pk,
                channel=channel,
                purpose=purpose,
                status=Consent.Status.GRANTED,
            ).first()
            if existing is not None:
                before = snapshot(existing)
                existing.source = source or existing.source
                existing.wording_version = wording_version or existing.wording_version
                if evidence is not None:
                    existing.evidence_json = evidence
                if ip_hash:
                    existing.ip_hash = ip_hash
                existing.save(
                    update_fields=[
                        "source",
                        "wording_version",
                        "evidence_json",
                        "ip_hash",
                        "updated_at",
                    ]
                )
                if before != snapshot(existing):
                    record_audit(
                        "messaging.consent_granted",
                        entity=existing,
                        before=before,
                        after=snapshot(existing),
                        actor=actor,
                        request=request,
                    )
                return existing

            consent = Consent.all_objects.create(
                clinic=clinic,
                patient=patient,
                channel=channel,
                purpose=purpose,
                status=Consent.Status.GRANTED,
                source=source,
                wording_version=wording_version,
                evidence_json=evidence or {},
                ip_hash=ip_hash,
                granted_at=moment,
            )
            record_audit(
                "messaging.consent_granted",
                entity=consent,
                after=snapshot(consent),
                actor=actor,
                request=request,
            )
            return consent


def revoke(
    consent: Consent,
    *,
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> Consent:
    """Revoke ``consent``; suppress all channels and skip queued messages."""

    clinic = consent.clinic
    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(clinic):
            if consent.status != Consent.Status.REVOKED:
                before = snapshot(consent)
                consent.status = Consent.Status.REVOKED
                consent.revoked_at = moment
                consent.save(update_fields=["status", "revoked_at", "updated_at"])
                record_audit(
                    "messaging.consent_revoked",
                    entity=consent,
                    before=before,
                    after=snapshot(consent),
                    actor=actor,
                    request=request,
                )

            suppression.record_opt_out(
                consent.patient,
                channel=consent.channel,
                reason=Suppression.Reason.MANUAL,
            )
            Message.all_objects.filter(
                clinic=clinic,
                patient_id=consent.patient_id,
                status=Message.Status.QUEUED,
            ).update(status=Message.Status.SKIPPED, reason="consent_revoked")
    return consent


__all__ = ["SNAPSHOT_FIELDS", "grant", "is_granted", "revoke", "snapshot"]
