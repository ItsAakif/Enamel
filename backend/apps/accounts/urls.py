"""Authentication URL routes (mounted under /api/v1/auth/)."""

from django.urls import path

from .views import (
    CsrfView,
    LoginView,
    LogoutView,
    MeView,
    MfaEnableView,
    MfaVerifyView,
    PasswordConfirmView,
    PasswordResetView,
    RegisterView,
)

app_name = "accounts"

urlpatterns = [
    path("csrf/", CsrfView.as_view(), name="csrf"),
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
    path("password/reset/", PasswordResetView.as_view(), name="password-reset"),
    path("password/confirm/", PasswordConfirmView.as_view(), name="password-confirm"),
    path("mfa/enable/", MfaEnableView.as_view(), name="mfa-enable"),
    path("mfa/verify/", MfaVerifyView.as_view(), name="mfa-verify"),
]
