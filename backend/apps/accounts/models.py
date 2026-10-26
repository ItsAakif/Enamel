"""Accounts identity models.

The custom user landed in T100 (UUIDv7 pk, email login). T101 adds the
security fields used by staff session auth: MFA state, the session epoch used
for revocation, and the password history / one-time reset token tables.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    last_login = models.DateTimeField(null=True, blank=True)

    # Optional TOTP MFA. The secret is Fernet-encrypted (see services.mfa).
    mfa_enabled = models.BooleanField(default=False)
    mfa_secret_enc = models.TextField(blank=True, default="")

    # Incremented to revoke every existing session for this user.
    session_epoch = models.PositiveBigIntegerField(default=0)
    password_changed_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "accounts_user"
        verbose_name = "User"
        verbose_name_plural = "Users"
        constraints = [
            # Emails are case-insensitive identities. The field-level unique
            # constraint stays, but it cannot catch "Owner@" vs "owner@".
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
        ]

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        # Direct ORM/admin writes bypass the manager; normalize here as well so
        # the functional unique index is the second line of defense.
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        return self.full_name or self.email

    def get_short_name(self) -> str:
        return self.full_name.split(" ")[0] if self.full_name else self.email

    def bump_session_epoch(self) -> int:
        """Revoke all sessions by advancing the epoch atomically."""

        User.objects.filter(pk=self.pk).update(session_epoch=models.F("session_epoch") + 1)
        self.refresh_from_db(fields=["session_epoch"])
        return self.session_epoch


class PasswordHistory(models.Model):
    """Hashes of recent passwords; the last three are checked for reuse."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="password_history",
    )
    password_hash = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_passwordhistory"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "-created_at"], name="pwhist_user_created_idx")]


class PasswordResetToken(models.Model):
    """A single-use, hashed password reset token (30 minute lifetime)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="password_reset_tokens",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_passwordresettoken"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"], name="pwreset_user_created_idx")]
