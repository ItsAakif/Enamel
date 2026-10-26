"""Registration and staff login orchestration."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import mfa
from apps.accounts.services import password as password_service
from apps.accounts.services.sessions import login_user
from apps.clinics.models import Membership
from apps.clinics.services.members import create_clinic_with_owner
from apps.core.audit import record as record_audit
from apps.core.audit import resolve_single_clinic
from apps.core.errors import ApiError


def register_clinic_owner(
    *,
    clinic_name: str,
    clinic_slug: str,
    timezone_name: str,
    currency: str,
    full_name: str,
    email: str,
    password_value: str,
) -> tuple[User, Any, Membership]:
    """Create user + clinic + owner membership + default location atomically."""

    normalized_email = (email or "").strip().lower()

    with transaction.atomic():
        user = User.objects.create_user(
            email=normalized_email,
            password=password_value,
            full_name=full_name.strip(),
        )
        user.password_changed_at = timezone.now()
        user.save(update_fields=["password_changed_at"])
        password_service.record_password_history(user)

        clinic, membership = create_clinic_with_owner(
            user,
            name=clinic_name.strip(),
            slug=clinic_slug.strip().lower(),
            timezone_name=timezone_name,
            currency=currency.upper(),
        )

    return user, clinic, membership


def authenticate_staff(
    request: Any,
    *,
    email: str,
    password_value: str,
    otp: str | None = None,
) -> User:
    """Authenticate credentials and optional TOTP, then start a session."""

    normalized_email = (email or "").strip().lower()
    user = authenticate(request=request, username=normalized_email, password=password_value)
    if user is None or not user.is_active:
        raise ApiError(
            "invalid_credentials",
            "Email or password is incorrect.",
            status_code=401,
        )

    if user.mfa_enabled:
        if not otp:
            raise ApiError(
                "mfa_required",
                "A one-time code is required.",
                status_code=400,
            )
        try:
            secret = mfa.decrypt_secret(user.mfa_secret_enc)
        except Exception:  # noqa: BLE001 - corrupt secret means MFA cannot pass
            secret = ""
        if not mfa.verify_totp(secret, otp):
            raise ApiError(
                "invalid_otp",
                "The one-time code is invalid.",
                status_code=401,
            )

    with transaction.atomic():
        login_user(request, user)

        # Only an unambiguous clinic is recorded: a user who belongs to several
        # clinics has no single tenant for this login, so the event is skipped
        # rather than filed against the wrong clinic (T103).
        clinic_id = resolve_single_clinic(user)
        if clinic_id is not None:
            record_audit(
                "auth.login",
                actor=user,
                clinic=clinic_id,
                request=request,
                entity_type="accounts.user",
                entity_id=str(user.pk),
            )
    return user

def active_memberships(user: User):
    """The active memberships of ``user``.

    The membership rows are addressed by the principal key, so the principal
    context is opened here: the login and the "who am I" reads happen before
    a clinic context exists, and the row-level policies resolve them by
    ``app.current_principal_id`` (ADR-013).

    The read runs inside its own transaction. The principal context is then
    transaction-local and the read cannot be separated from it: a pooled
    connection that is recycled between the two statements would otherwise
    answer the read with the wrong principal and return **no** membership -
    the login response would silently lose the caller's clinics.
    """

    from apps.core.rls import rls_context

    with transaction.atomic():
        with rls_context(None, principal_id=getattr(user, "pk", None)):
            return list(
                Membership.all_objects.filter(user=user, status=Membership.Status.ACTIVE)
                .select_related("clinic")
                .order_by("created_at", "id")
            )


def active_clinics(user: User) -> list[Any]:
    return [membership.clinic for membership in active_memberships(user)]
