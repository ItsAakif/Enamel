"""Session lifecycle: login, logout, rotation and epoch revocation."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth import login, logout
from django.db import models
from django.utils import timezone

from apps.accounts.models import User

AUTH_BACKEND = "django.contrib.auth.backends.ModelBackend"


def login_user(request: Any, user: User) -> None:
    """Start a fresh staff session, rotating the session key.

    The session is flushed first so a pre-login session (or a fixation
    attempt) cannot be promoted. ``_session_started_at`` anchors the absolute
    12 hour limit; ``_last_activity`` anchors the 30 minute idle limit and
    ``_session_epoch`` ties the session to the user's revocation counter.
    """

    request.session.flush()
    login(request, user, backend=AUTH_BACKEND)

    now = timezone.now().timestamp()
    request.session["_session_started_at"] = now
    request.session["_last_activity"] = now
    request.session["_session_epoch"] = user.session_epoch
    request.session.set_expiry(settings.SESSION_COOKIE_AGE)


def logout_user(request: Any) -> None:
    request.session.flush()
    logout(request)


def bump_session_epoch(user: User) -> int:
    """Invalidate every session for ``user`` by advancing its epoch."""

    User.objects.filter(pk=user.pk).update(
        session_epoch=models.F("session_epoch") + 1
    )
    user.refresh_from_db(fields=["session_epoch"])
    return user.session_epoch


def revoke_all_sessions(user: User) -> int:
    return bump_session_epoch(user)
