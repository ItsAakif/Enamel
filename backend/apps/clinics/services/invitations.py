"""Invitation creation, acceptance and revocation.

Tokens are 32 random bytes, stored as SHA-256, expire after seven days, are
bound to the invited email and are single use. The raw token is only ever in
the email (and in the API response when ``DEBUG`` is on).
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from apps.accounts.services.password import hash_token
from apps.clinics.models import Invitation, Membership
from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.rls import set_rls_context
from apps.core.tenancy import with_clinic


def _send_invitation_email(invitation: Invitation, raw_token: str) -> None:
    base_url = getattr(settings, "FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")
    accept_url = f"{base_url}/app/invitations/accept?token={raw_token}"
    send_mail(
        subject=f"You are invited to join {invitation.clinic.name}",
        message=(
            f"You have been invited to join {invitation.clinic.name} on Enamel "
            f"as a {invitation.role}.\n\n"
            f"{accept_url}\n\n"
            "The invitation expires in 7 days and only works for this email address."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[invitation.email],
        fail_silently=False,
    )


@transaction.atomic
def create_invitation(
    clinic: Any,
    *,
    email: str,
    role: str,
    actor: Any,
    request: Any | None = None,
) -> tuple[Invitation, str]:
    normalized = (email or "").strip().lower()
    now = timezone.now()

    # Re-inviting invalidates every earlier pending token for this address.
    Invitation.objects.filter(
        clinic=clinic,
        email=normalized,
        accepted_at__isnull=True,
        revoked_at__isnull=True,
    ).update(revoked_at=now)

    raw_token = secrets.token_urlsafe(32)
    invitation = Invitation.objects.create(
        clinic=clinic,
        email=normalized,
        role=role,
        token_hash=hash_token(raw_token),
        expires_at=now + timedelta(days=int(getattr(settings, "INVITATION_TTL_DAYS", 7))),
        created_by=actor,
    )

    record_audit(
        "invitation.created",
        entity=invitation,
        after={
            "email": invitation.email,
            "role": invitation.role,
            "expires_at": invitation.expires_at,
        },
        actor=actor,
        request=request,
    )

    _send_invitation_email(invitation, raw_token)
    return invitation, raw_token


@transaction.atomic
def accept_invitation(user: Any, raw_token: str, request: Any | None = None) -> Membership:
    token_hash = hash_token((raw_token or "").strip())
    now = timezone.now()

    # The presented token is the capability that resolves the invitation: it is
    # set before the lookup, so the row is visible without a clinic context
    # (T137).
    set_rls_context(invitation_token_hash=token_hash)

    # 1. Resolve the invitation by capability with a **plain** SELECT. The
    #    capability policy is SELECT-scoped and PostgreSQL applies the UPDATE
    #    policies to a locking clause, so a ``select_for_update()`` here would
    #    filter the row out under the runtime role and the accept path would
    #    answer 404 (ADR-013, review finding F-02).
    invitation = (
        Invitation.objects.select_related("clinic").filter(token_hash=token_hash).first()
    )
    if invitation is None:
        raise ApiError("not_found", "Invitation not found.", status_code=404)
    if invitation.revoked_at is not None:
        raise ApiError(
            "invitation_revoked",
            "This invitation has been revoked.",
            status_code=410,
        )
    if invitation.accepted_at is not None:
        raise ApiError(
            "invitation_used",
            "This invitation has already been accepted.",
            status_code=409,
        )
    if invitation.expires_at <= now:
        raise ApiError(
            "invitation_expired",
            "This invitation has expired.",
            status_code=410,
        )
    if invitation.email.lower() != (user.email or "").lower():
        raise ApiError(
            "invitation_email_mismatch",
            "This invitation was sent to a different email address.",
            status_code=403,
        )

    # 2. The clinic is known now, so the row can be locked **inside the clinic
    #    context**: the tenant policy covers the locking clause there, and the
    #    writes that follow are scoped to the clinic.
    with with_clinic(invitation.clinic):
        locked = (
            Invitation.objects.select_for_update()
            .filter(pk=invitation.pk, token_hash=token_hash)
            .first()
        )
        if locked is None:
            raise ApiError("not_found", "Invitation not found.", status_code=404)
        # The state is re-checked under the lock: a concurrent accept must not
        # create a second membership.
        if locked.revoked_at is not None:
            raise ApiError(
                "invitation_revoked",
                "This invitation has been revoked.",
                status_code=410,
            )
        if locked.accepted_at is not None:
            raise ApiError(
                "invitation_used",
                "This invitation has already been accepted.",
                status_code=409,
            )
        if locked.expires_at <= now:
            raise ApiError(
                "invitation_expired",
                "This invitation has expired.",
                status_code=410,
            )

        membership = (
            Membership.all_objects.select_for_update()
            .filter(clinic_id=locked.clinic_id, user=user)
            .first()
        )

        if membership is not None and membership.is_active:
            raise ApiError(
                "already_member",
                "You are already a member of this clinic.",
                status_code=409,
            )

        if membership is None:
            membership = Membership.objects.create(
                clinic=locked.clinic,
                user=user,
                role=locked.role,
                status=Membership.Status.ACTIVE,
                invited_by=locked.created_by,
                permission_overrides=[],
                accepted_at=now,
            )
        else:
            membership.role = locked.role
            membership.status = Membership.Status.ACTIVE
            membership.invited_by = locked.created_by
            membership.permission_overrides = []
            membership.accepted_at = now
            membership.save(
                update_fields=[
                    "role",
                    "status",
                    "invited_by",
                    "permission_overrides",
                    "accepted_at",
                    "updated_at",
                ]
            )

        # The invitation row and its audit event belong to the clinic, so both
        # writes happen inside the clinic context.
        locked.accepted_at = now
        locked.save(update_fields=["accepted_at"])
        record_audit(
            "invitation.accepted",
            entity=locked,
            after={
                "role": locked.role,
                "membership_id": str(membership.id),
                "accepted_at": locked.accepted_at,
            },
            actor=user,
            request=request,
        )
    return membership


@transaction.atomic
def revoke_invitation(
    invitation: Invitation,
    *,
    actor: Any | None = None,
    request: Any | None = None,
) -> None:
    if invitation.accepted_at is not None:
        raise ApiError(
            "invitation_used",
            "This invitation has already been accepted.",
            status_code=409,
        )
    if invitation.revoked_at is None:
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at"])
        record_audit(
            "invitation.revoked",
            entity=invitation,
            after={"role": invitation.role, "revoked_at": invitation.revoked_at},
            actor=actor,
            request=request,
        )


__all__ = ["accept_invitation", "create_invitation", "revoke_invitation"]
