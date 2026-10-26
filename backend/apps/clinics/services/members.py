"""Clinic and membership services.

All writes that can race (registration, last-owner checks, role changes) are
wrapped in ``transaction.atomic`` and lock the owner rows that the invariant
depends on.
"""

from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.services.sessions import bump_session_epoch
from apps.clinics.models import Clinic, Location, Membership, default_location_name
from apps.clinics.permissions import validate_overrides
from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.tenancy import with_clinic


@transaction.atomic
def create_default_location(clinic: Clinic) -> Location:
    with with_clinic(clinic):
        return Location.objects.create(name=default_location_name())


@transaction.atomic
def create_membership(
    clinic: Clinic,
    user: Any,
    *,
    role: str = Membership.Role.ASSISTANT,
    status: str = Membership.Status.ACTIVE,
    invited_by: Any | None = None,
    permission_overrides: list[str] | None = None,
) -> Membership:
    with with_clinic(clinic):
        return Membership.objects.create(
            clinic=clinic,
            user=user,
            role=role,
            status=status,
            invited_by=invited_by,
            permission_overrides=permission_overrides or [],
            accepted_at=timezone.now() if status == Membership.Status.ACTIVE else None,
        )


def create_clinic_with_owner(
    user: Any,
    *,
    name: str,
    slug: str,
    timezone_name: str = "Asia/Kolkata",
    currency: str = "INR",
    locale: str = "en-IN",
    country: str = "IN",
    phone: str = "",
    address_json: dict[str, Any] | None = None,
) -> tuple[Clinic, Membership]:
    """Create a clinic, its default location and the owner membership."""

    try:
        with transaction.atomic():
            clinic = Clinic.objects.create(
                name=name,
                slug=slug,
                timezone=timezone_name,
                currency=currency,
                locale=locale,
                country=country,
                phone=phone,
                address_json=address_json or {},
            )
            create_default_location(clinic)
            membership = create_membership(
                clinic,
                user,
                role=Membership.Role.OWNER,
                status=Membership.Status.ACTIVE,
            )
            return clinic, membership
    except IntegrityError:
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={"slug": ["A clinic with this slug already exists."]},
        ) from None


def _lock_active_owner_ids(clinic_id: Any) -> list[Any]:
    return list(
        Membership.all_objects.select_for_update()
        .filter(
            clinic_id=clinic_id,
            role=Membership.Role.OWNER,
            status=Membership.Status.ACTIVE,
        )
        .values_list("id", flat=True)
    )


def _guard_last_owner(
    membership: Membership,
    *,
    new_role: str,
    new_status: str,
    owner_ids: list[Any],
) -> None:
    if (
        membership.id in owner_ids
        and membership.role == Membership.Role.OWNER
        and membership.status == Membership.Status.ACTIVE
        and (new_role != Membership.Role.OWNER or new_status != Membership.Status.ACTIVE)
        and len(owner_ids) <= 1
    ):
        raise ApiError(
            "last_owner",
            "A clinic must keep at least one active owner.",
            status_code=409,
        )


@transaction.atomic
def update_membership(
    membership: Membership,
    *,
    role: str | None = None,
    status: str | None = None,
    permission_overrides: list[str] | None = None,
    actor: Any | None = None,
    request: Any | None = None,
) -> Membership:
    locked = (
        Membership.all_objects.select_for_update()
        .select_related("user")
        .get(pk=membership.pk)
    )
    owner_ids = _lock_active_owner_ids(locked.clinic_id)

    new_role = role or locked.role
    new_status = status or locked.status
    _guard_last_owner(
        locked,
        new_role=new_role,
        new_status=new_status,
        owner_ids=owner_ids,
    )

    update_fields: list[str] = []
    previous_role = locked.role
    previous_status = locked.status
    previous_overrides = list(locked.permission_overrides or [])
    privilege_changed = new_role != locked.role or new_status != locked.status

    if new_role != locked.role:
        locked.role = new_role
        update_fields.append("role")
    if new_status != locked.status:
        locked.status = new_status
        update_fields.append("status")
        if new_status == Membership.Status.ACTIVE and locked.accepted_at is None:
            locked.accepted_at = timezone.now()
            update_fields.append("accepted_at")
    if permission_overrides is not None:
        locked.permission_overrides = validate_overrides(permission_overrides)
        update_fields.append("permission_overrides")

    if update_fields:
        update_fields.append("updated_at")
        locked.save(update_fields=update_fields)
    if privilege_changed:
        bump_session_epoch(locked.user)

    # One audit row per changed aspect, so an investigator can query the exact
    # action (``membership.role_changed``) instead of diffing a combined event.
    if previous_role != locked.role:
        record_audit(
            "membership.role_changed",
            entity=locked,
            before={"role": previous_role},
            after={"role": locked.role},
            actor=actor,
            request=request,
        )
    if previous_status != locked.status:
        record_audit(
            "membership.status_changed",
            entity=locked,
            before={"status": previous_status},
            after={"status": locked.status},
            actor=actor,
            request=request,
        )
    if permission_overrides is not None and previous_overrides != list(locked.permission_overrides):
        record_audit(
            "membership.permissions_changed",
            entity=locked,
            before={"permission_overrides": previous_overrides},
            after={"permission_overrides": list(locked.permission_overrides)},
            actor=actor,
            request=request,
        )
    return locked


@transaction.atomic
def remove_membership(
    membership: Membership,
    *,
    actor: Any | None = None,
    request: Any | None = None,
) -> None:
    locked = (
        Membership.all_objects.select_for_update()
        .select_related("user")
        .get(pk=membership.pk)
    )
    owner_ids = _lock_active_owner_ids(locked.clinic_id)
    _guard_last_owner(
        locked,
        new_role=locked.role,
        new_status=Membership.Status.REMOVED,
        owner_ids=owner_ids,
    )
    previous_status = locked.status
    locked.status = Membership.Status.REMOVED
    locked.save(update_fields=["status", "updated_at"])
    bump_session_epoch(locked.user)
    record_audit(
        "membership.removed",
        entity=locked,
        before={"status": previous_status},
        after={"status": Membership.Status.REMOVED},
        actor=actor,
        request=request,
    )


__all__ = [
    "create_clinic_with_owner",
    "create_default_location",
    "create_membership",
    "remove_membership",
    "update_membership",
]
