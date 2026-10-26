"""Authentication endpoint tests: registration, sessions, reset, MFA."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

import pyotp
import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone
from freezegun import freeze_time

from apps.accounts.models import PasswordHistory, PasswordResetToken, User
from apps.accounts.services.mfa import decrypt_secret
from apps.accounts.services.sessions import bump_session_epoch
from apps.clinics.models import Clinic, Location, Membership

pytestmark = pytest.mark.django_db

TOKEN_RE = re.compile(r"[?&]token=([A-Za-z0-9_\-]+)")


def register_payload(**overrides):
    payload = {
        "clinic_name": "Bright Smile Dental",
        "clinic_slug": "bright-smile",
        "timezone": "Asia/Kolkata",
        "currency": "INR",
        "full_name": "Ada Owner",
        "email": "owner@bright.test",
        "password": "Str0ngPass!2026",
    }
    payload.update(overrides)
    return payload


def token_from_mail(mailoutbox) -> str:
    assert mailoutbox, "expected an outbound email"
    match = TOKEN_RE.search(mailoutbox[-1].body)
    assert match, "email must contain a token"
    return match.group(1)


def wrong_otp(secret: str) -> str:
    totp = pyotp.TOTP(secret)
    for candidate in ("000000", "999999", "123456", "654321"):
        if not totp.verify(candidate, valid_window=1):
            return candidate
    raise AssertionError("could not find an invalid OTP")


def test_csrf_endpoint_sets_cookie(api_client):
    response = api_client.get("/api/v1/auth/csrf/")

    assert response.status_code == 204
    assert settings.CSRF_COOKIE_NAME in response.cookies


def test_register_creates_tenant_and_logs_in(api_client):
    response = api_client.post(
        "/api/v1/auth/register/",
        register_payload(),
        format="json",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "owner@bright.test"
    assert body["clinic"]["slug"] == "bright-smile"
    assert body["membership"]["role"] == "owner"

    clinic = Clinic.objects.get(slug="bright-smile")
    assert body["clinic"]["id"] == str(clinic.id)
    assert Location.all_objects.filter(clinic=clinic).count() == 1
    assert Location.all_objects.get(clinic=clinic).name == "Main location"
    assert Membership.all_objects.get(clinic=clinic).role == "owner"
    assert PasswordHistory.objects.filter(user__email="owner@bright.test").count() == 1

    me = api_client.get("/api/v1/auth/me/")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "owner@bright.test"


def test_register_rejects_duplicate_clinic_slug_as_field_error(api_client, clinic_factory):
    clinic_factory(slug="bright-smile")
    response = api_client.post(
        "/api/v1/auth/register/",
        register_payload(email="other@bright.test"),
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert "clinic_slug" in response.json()["error"]["field_errors"]


def test_register_is_atomic_when_membership_creation_fails(api_client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("membership failure")

    monkeypatch.setattr(Membership.objects, "create", boom)

    # T102 centralises unhandled failures into the generic 500 envelope instead
    # of leaking the exception to the test client. The atomicity assertion is
    # unchanged: the registration transaction must still roll back.
    response = api_client.post("/api/v1/auth/register/", register_payload(), format="json")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert b"membership failure" not in response.content

    assert not User.objects.filter(email="owner@bright.test").exists()
    assert not Clinic.objects.filter(slug="bright-smile").exists()
    assert not Location.all_objects.filter(clinic__slug="bright-smile").exists()


def test_login_sets_session_cookie_and_returns_clinics(
    api_client, user_factory, clinic_factory, membership_factory
):
    user = user_factory("login@bright.test")
    clinic = clinic_factory(slug="login-clinic")
    membership_factory(clinic, user)

    response = api_client.post(
        "/api/v1/auth/login/",
        {"email": "login@bright.test", "password": "Str0ngPass!2026"},
        format="json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "login@bright.test"
    assert [clinic["slug"] for clinic in body["clinics"]] == ["login-clinic"]
    assert body["memberships"][0]["role"] == "owner"

    cookie = response.cookies[settings.SESSION_COOKIE_NAME]
    assert cookie["httponly"] is True
    assert cookie["samesite"] == "Lax"


def test_login_is_case_insensitive_for_email(
    api_client, user_factory, clinic_factory, membership_factory
):
    user = user_factory("mixed@bright.test")
    clinic = clinic_factory(slug="mixed-clinic")
    membership_factory(clinic, user)

    response = api_client.post(
        "/api/v1/auth/login/",
        {"email": "MiXeD@Bright.TEST", "password": "Str0ngPass!2026"},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "mixed@bright.test"


def test_login_rejects_missing_csrf_token_when_enforced(user_factory):
    user_factory("csrf@bright.test")
    payload = json.dumps(
        {"email": "csrf@bright.test", "password": "Str0ngPass!2026"}
    )
    client = Client(enforce_csrf_checks=True)

    denied = client.post(
        "/api/v1/auth/login/",
        data=payload,
        content_type="application/json",
    )
    assert denied.status_code == 403

    csrf_response = client.get("/api/v1/auth/csrf/")
    assert csrf_response.status_code == 204
    token = csrf_response.cookies[settings.CSRF_COOKIE_NAME].value

    allowed = client.post(
        "/api/v1/auth/login/",
        data=payload,
        content_type="application/json",
        headers={"X-CSRFToken": token},
    )
    assert allowed.status_code == 200
    assert allowed.json()["user"]["email"] == "csrf@bright.test"


def test_login_rotates_session_key(
    api_client, user_factory, clinic_factory, membership_factory
):
    user = user_factory("rotate@bright.test")
    clinic = clinic_factory(slug="rotate-clinic")
    membership_factory(clinic, user)

    api_client.post(
        "/api/v1/auth/login/",
        {"email": "rotate@bright.test", "password": "Str0ngPass!2026"},
        format="json",
    )
    first_key = api_client.session.session_key

    api_client.post(
        "/api/v1/auth/login/",
        {"email": "rotate@bright.test", "password": "Str0ngPass!2026"},
        format="json",
    )
    second_key = api_client.session.session_key

    assert first_key
    assert second_key
    assert first_key != second_key


def test_login_returns_generic_invalid_credentials(api_client, user_factory):
    user_factory("wrong@bright.test")

    response = api_client.post(
        "/api/v1/auth/login/",
        {"email": "wrong@bright.test", "password": "not-the-password"},
        format="json",
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"
    assert "user" not in response.json()


def test_login_is_throttled_after_five_attempts(api_client, user_factory):
    user_factory("throttle@bright.test")

    statuses = []
    for _ in range(6):
        response = api_client.post(
            "/api/v1/auth/login/",
            {"email": "throttle@bright.test", "password": "wrong-password"},
            format="json",
        )
        statuses.append(response.status_code)

    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429
    assert response.json()["error"]["code"] == "throttled"


def test_logout_flushes_session(
    api_client, user_factory, clinic_factory, membership_factory
):
    user = user_factory("logout@bright.test")
    clinic = clinic_factory(slug="logout-clinic")
    membership_factory(clinic, user)
    api_client.post(
        "/api/v1/auth/login/",
        {"email": "logout@bright.test", "password": "Str0ngPass!2026"},
        format="json",
    )

    logout = api_client.post("/api/v1/auth/logout/")
    assert logout.status_code == 204

    me = api_client.get("/api/v1/auth/me/")
    assert me.status_code == 401
    assert me.json()["error"]["code"] == "unauthenticated"


def test_session_expires_after_thirty_minutes_idle(
    api_client, user_factory, clinic_factory, membership_factory
):
    user = user_factory("idle@bright.test")
    clinic = clinic_factory(slug="idle-clinic")
    membership_factory(clinic, user)

    with freeze_time("2026-10-18 10:00:00") as frozen:
        api_client.post(
            "/api/v1/auth/login/",
            {"email": "idle@bright.test", "password": "Str0ngPass!2026"},
            format="json",
        )
        frozen.tick(timedelta(minutes=31))

        response = api_client.get("/api/v1/auth/me/")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_expired"


def test_session_expires_after_twelve_hours_absolute(
    api_client, user_factory, clinic_factory, membership_factory
):
    user = user_factory("absolute@bright.test")
    clinic = clinic_factory(slug="absolute-clinic")
    membership_factory(clinic, user)

    with freeze_time("2026-10-18 10:00:00") as frozen:
        api_client.post(
            "/api/v1/auth/login/",
            {"email": "absolute@bright.test", "password": "Str0ngPass!2026"},
            format="json",
        )
        # Keep the session active so idle expiry cannot be the cause.
        frozen.tick(timedelta(minutes=20))
        api_client.get("/api/v1/auth/me/")
        frozen.tick(timedelta(hours=12))

        response = api_client.get("/api/v1/auth/me/")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_expired"


def test_epoch_bump_revokes_existing_session(
    api_client, user_factory, clinic_factory, membership_factory
):
    user = user_factory("epoch@bright.test")
    clinic = clinic_factory(slug="epoch-clinic")
    membership_factory(clinic, user)
    api_client.post(
        "/api/v1/auth/login/",
        {"email": "epoch@bright.test", "password": "Str0ngPass!2026"},
        format="json",
    )
    assert api_client.get("/api/v1/auth/me/").status_code == 200

    bump_session_epoch(user)

    response = api_client.get("/api/v1/auth/me/")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_expired"


def test_me_with_two_memberships_without_header(
    api_client, user_factory, clinic_factory, membership_factory
):
    user = user_factory("multi@bright.test")
    first = clinic_factory(slug="multi-one")
    second = clinic_factory(slug="multi-two")
    membership_factory(first, user)
    membership_factory(second, user)
    api_client.force_login(user)

    response = api_client.get("/api/v1/auth/me/")

    assert response.status_code == 200
    assert len(response.json()["memberships"]) == 2
    assert response.json()["current_clinic"] is None


def test_password_reset_sends_hashed_single_use_token(
    api_client, user_factory, mailoutbox
):
    user = user_factory("reset@bright.test")

    with freeze_time("2026-10-18 10:00:00"):
        response = api_client.post(
            "/api/v1/auth/password/reset/",
            {"email": "reset@bright.test"},
            format="json",
        )

        assert response.status_code == 202
        assert response.json() == {"status": "accepted"}
        raw_token = token_from_mail(mailoutbox)

        reset = PasswordResetToken.objects.get(user=user)
        assert reset.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
        assert raw_token not in reset.token_hash
        assert reset.expires_at == timezone.now() + timedelta(minutes=30)


def test_password_reset_is_generic_for_unknown_email(api_client, mailoutbox):
    response = api_client.post(
        "/api/v1/auth/password/reset/",
        {"email": "nobody@bright.test"},
        format="json",
    )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    assert mailoutbox == []


def test_password_confirm_revokes_sessions_and_enforces_history(
    api_client, user_factory, mailoutbox
):
    user = user_factory("confirm@bright.test")
    api_client.force_login(user)
    assert api_client.get("/api/v1/auth/me/").status_code == 200

    api_client.post(
        "/api/v1/auth/password/reset/",
        {"email": "confirm@bright.test"},
        format="json",
    )
    raw_token = token_from_mail(mailoutbox)

    confirm = api_client.post(
        "/api/v1/auth/password/confirm/",
        {"token": raw_token, "new_password": "BrandNewPass!2026"},
        format="json",
    )
    assert confirm.status_code == 204

    # All sessions are revoked.
    assert api_client.get("/api/v1/auth/me/").status_code == 401
    user.refresh_from_db()
    assert user.check_password("BrandNewPass!2026")
    assert user.password_changed_at is not None

    # Old password no longer works; the new one does.
    old_login = api_client.post(
        "/api/v1/auth/login/",
        {"email": "confirm@bright.test", "password": "Str0ngPass!2026"},
        format="json",
    )
    assert old_login.status_code == 401
    new_login = api_client.post(
        "/api/v1/auth/login/",
        {"email": "confirm@bright.test", "password": "BrandNewPass!2026"},
        format="json",
    )
    assert new_login.status_code == 200

    # The used token is dead.
    replay = api_client.post(
        "/api/v1/auth/password/confirm/",
        {"token": raw_token, "new_password": "AnotherPass!2026"},
        format="json",
    )
    assert replay.status_code == 400
    assert replay.json()["error"]["code"] == "invalid_token"

    # Reusing the current password is rejected.
    api_client.post(
        "/api/v1/auth/password/reset/",
        {"email": "confirm@bright.test"},
        format="json",
    )
    reuse_token = token_from_mail(mailoutbox)
    reuse = api_client.post(
        "/api/v1/auth/password/confirm/",
        {"token": reuse_token, "new_password": "BrandNewPass!2026"},
        format="json",
    )
    assert reuse.status_code == 400
    assert reuse.json()["error"]["code"] == "password_reuse"


def test_password_reset_token_expires_after_thirty_minutes(
    api_client, user_factory, mailoutbox
):
    user_factory("expired@bright.test")

    with freeze_time("2026-10-18 10:00:00") as frozen:
        api_client.post(
            "/api/v1/auth/password/reset/",
            {"email": "expired@bright.test"},
            format="json",
        )
        raw_token = token_from_mail(mailoutbox)
        frozen.tick(timedelta(minutes=31))

        response = api_client.post(
            "/api/v1/auth/password/confirm/",
            {"token": raw_token, "new_password": "AnotherPass!2026"},
            format="json",
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_token"


def test_mfa_enrollment_and_login(
    api_client, user_factory, clinic_factory, membership_factory
):
    user = user_factory("mfa@bright.test")
    clinic = clinic_factory(slug="mfa-clinic")
    membership_factory(clinic, user)
    api_client.force_login(user)

    enable = api_client.post("/api/v1/auth/mfa/enable/")
    assert enable.status_code == 200
    secret = enable.json()["secret"]
    assert enable.json()["provisioning_uri"].startswith("otpauth://totp/")

    user.refresh_from_db()
    assert user.mfa_secret_enc
    assert secret not in user.mfa_secret_enc
    assert decrypt_secret(user.mfa_secret_enc) == secret
    assert user.mfa_enabled is False

    verify = api_client.post(
        "/api/v1/auth/mfa/verify/",
        {"otp": pyotp.TOTP(secret).now()},
        format="json",
    )
    assert verify.status_code == 204
    user.refresh_from_db()
    assert user.mfa_enabled is True

    enable_again = api_client.post("/api/v1/auth/mfa/enable/")
    assert enable_again.status_code == 409
    assert enable_again.json()["error"]["code"] == "mfa_already_enabled"

    api_client.post("/api/v1/auth/logout/")

    missing = api_client.post(
        "/api/v1/auth/login/",
        {"email": "mfa@bright.test", "password": "Str0ngPass!2026"},
        format="json",
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "mfa_required"

    invalid = api_client.post(
        "/api/v1/auth/login/",
        {
            "email": "mfa@bright.test",
            "password": "Str0ngPass!2026",
            "otp": wrong_otp(secret),
        },
        format="json",
    )
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "invalid_otp"

    valid = api_client.post(
        "/api/v1/auth/login/",
        {
            "email": "mfa@bright.test",
            "password": "Str0ngPass!2026",
            "otp": pyotp.TOTP(secret).now(),
        },
        format="json",
    )
    assert valid.status_code == 200
