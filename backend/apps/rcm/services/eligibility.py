"""Eligibility and policy resolution (T134).

Policy resolution is date-aware: for a service date it returns the policy whose
validity window covers that date and which is active. An expired or
not-yet-effective policy is reported by :func:`policy_status` so a caller can
flag it rather than silently using the wrong cover.

The eligibility probe is delegated to the configured adapter and the result is
always stored -- including failures, which keep the adapter's ``error_code``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.audit import record as record_audit
from apps.core.tenancy import with_clinic

from ..adapters import resolve_adapter
from ..models import EligibilityCheck, InsurancePolicy

STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"
STATUS_NOT_YET_EFFECTIVE = "not_yet_effective"
STATUS_INACTIVE = "inactive"


def policy_status(policy: InsurancePolicy, service_date: date) -> str:
    """Classify ``policy`` relative to ``service_date``."""

    if not policy.is_active:
        return STATUS_INACTIVE
    if policy.valid_from and policy.valid_from > service_date:
        return STATUS_NOT_YET_EFFECTIVE
    if policy.valid_to and policy.valid_to < service_date:
        return STATUS_EXPIRED
    return STATUS_ACTIVE


def resolve_policy(
    patient: Any,
    service_date: date,
    *,
    payer: Any = None,
) -> InsurancePolicy | None:
    """Return the active policy covering ``service_date`` (or ``None``)."""

    queryset = InsurancePolicy.all_objects.filter(
        clinic_id=patient.clinic_id, patient_id=patient.pk
    )
    if payer is not None:
        queryset = queryset.filter(payer=payer)
    for policy in queryset.order_by("-valid_from", "-id"):
        if policy_status(policy, service_date) == STATUS_ACTIVE:
            return policy
    return None


def latest_policy(patient: Any, *, payer: Any = None) -> InsurancePolicy | None:
    """Return the most recent policy (used to *flag* an expired one)."""

    queryset = InsurancePolicy.all_objects.filter(
        clinic_id=patient.clinic_id, patient_id=patient.pk
    )
    if payer is not None:
        queryset = queryset.filter(payer=payer)
    return queryset.order_by("-valid_from", "-id").first()


def check(
    policy: InsurancePolicy,
    service_date: date,
    *,
    adapter: Any = None,
    actor: Any = None,
    request: Any = None,
    now: datetime | None = None,
) -> EligibilityCheck:
    """Run one eligibility probe for ``policy`` and store the result."""

    chosen = adapter or resolve_adapter()
    result = chosen.check_eligibility(policy, service_date)
    moment = now or timezone.now()
    with transaction.atomic():
        with with_clinic(policy.clinic):
            record = EligibilityCheck.all_objects.create(
                clinic=policy.clinic,
                policy=policy,
                service_date=service_date,
                status=result.status,
                response_json=result.response,
                error_code=result.error_code,
                checked_at=moment,
            )
            record_audit(
                "rcm.eligibility_checked",
                entity=record,
                after={
                    "policy_id": str(policy.pk),
                    "service_date": service_date.isoformat(),
                    "status": result.status,
                    "error_code": result.error_code,
                },
                actor=actor,
                request=request,
            )
    return record


__all__ = [
    "STATUS_ACTIVE",
    "STATUS_EXPIRED",
    "STATUS_INACTIVE",
    "STATUS_NOT_YET_EFFECTIVE",
    "check",
    "latest_policy",
    "policy_status",
    "resolve_policy",
]
