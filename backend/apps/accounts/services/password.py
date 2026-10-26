"""Password reset and history rules.

Tokens are 32 random bytes, stored as SHA-256 only, single use, and expire
after 30 minutes. Confirming a reset validates the new password against Django
validators plus the last three hashes and advances ``session_epoch`` so every
existing session stops working.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import PasswordHistory, PasswordResetToken, User
from apps.core.audit import record as record_audit
from apps.core.audit import resolve_single_clinic
from apps.core.errors import ApiError


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def new_raw_token() -> str:
    return secrets.token_urlsafe(32)


def _validate_new_password(user: User, new_password: str) -> None:
    try:
        validate_password(new_password, user=user)
    except ValidationError as exc:
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={"new_password": list(exc.messages)},
        ) from None

    recent = PasswordHistory.objects.filter(user=user).order_by("-created_at", "-id")[:3]
    for entry in recent:
        if check_password(new_password, entry.password_hash):
            raise ApiError(
                "password_reuse",
                "You cannot reuse one of your last three passwords.",
                status_code=400,
                field_errors={"new_password": ["cannot reuse a recent password"]},
            )


def record_password_history(user: User) -> PasswordHistory:
    return PasswordHistory.objects.create(user=user, password_hash=user.password)


def _send_reset_email(user: User, raw_token: str) -> None:
    base_url = getattr(settings, "FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")
    reset_url = f"{base_url}/reset-password?token={raw_token}"
    send_mail(
        subject="Reset your Enamel password",
        message=(
            "Use the link below to reset your Enamel password.\n\n"
            f"{reset_url}\n\n"
            "The link expires in 30 minutes and can only be used once. "
            "If you did not request this, ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


def request_password_reset(email: str, request: Any | None = None) -> None:
    """Create a reset token and send the email. Never reveals account existence."""

    normalized = (email or "").strip().lower()
    if not normalized:
        return

    user = User.objects.filter(email=normalized, is_active=True).first()
    if user is None:
        return

    raw_token = new_raw_token()
    ttl = int(getattr(settings, "PASSWORD_RESET_TIMEOUT_MINUTES", 30))

    with transaction.atomic():
        PasswordResetToken.objects.filter(user=user, used_at__isnull=True).update(
            used_at=timezone.now()
        )
        PasswordResetToken.objects.create(
            user=user,
            token_hash=hash_token(raw_token),
            expires_at=timezone.now() + timedelta(minutes=ttl),
        )
        record_audit(
            "auth.password_reset_requested",
            actor=user,
            clinic=resolve_single_clinic(user),
            request=request,
            entity_type="accounts.user",
            entity_id=str(user.pk),
        )

    _send_reset_email(user, raw_token)


def confirm_password_reset(
    token: str,
    new_password: str,
    request: Any | None = None,
) -> User:
    token_hash = hash_token((token or "").strip())
    now = timezone.now()

    with transaction.atomic():
        reset = (
            PasswordResetToken.objects.select_for_update()
            .select_related("user")
            .filter(token_hash=token_hash)
            .first()
        )
        if reset is None or reset.used_at is not None or reset.expires_at <= now:
            raise ApiError(
                "invalid_token",
                "The password reset link is invalid or has expired.",
                status_code=400,
            )

        user = User.objects.select_for_update().get(pk=reset.user_id)
        _validate_new_password(user, new_password)

        user.set_password(new_password)
        user.password_changed_at = now
        user.save(update_fields=["password", "password_changed_at"])
        record_password_history(user)

        reset.used_at = now
        reset.save(update_fields=["used_at"])

        User.objects.filter(pk=user.pk).update(
            session_epoch=models.F("session_epoch") + 1
        )
        user.refresh_from_db(fields=["session_epoch"])

        record_audit(
            "auth.password_reset_confirmed",
            actor=user,
            clinic=resolve_single_clinic(user),
            request=request,
            entity_type="accounts.user",
            entity_id=str(user.pk),
        )

    return user
