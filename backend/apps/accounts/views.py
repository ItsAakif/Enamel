"""Authentication and account HTTP views."""

from __future__ import annotations

from django.db import transaction
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.serializers import (
    LoginSerializer,
    MfaVerifySerializer,
    PasswordConfirmSerializer,
    PasswordResetSerializer,
    RegisterSerializer,
    UserSerializer,
)
from apps.accounts.services.auth import (
    active_memberships,
    authenticate_staff,
    register_clinic_owner,
)
from apps.accounts.services.mfa import (
    decrypt_secret,
    encrypt_secret,
    generate_secret,
    provisioning_uri,
    verify_totp,
)
from apps.accounts.services.password import confirm_password_reset, request_password_reset
from apps.accounts.services.sessions import login_user, logout_user
from apps.clinics.serializers import (
    ClinicSerializer,
    MembershipSerializer,
    MembershipWithClinicSerializer,
)
from apps.core.audit import record as record_audit
from apps.core.audit import resolve_single_clinic
from apps.core.errors import ApiError
from apps.core.throttles import AuthThrottle

# Response shapes are documented with inline serializers because the views
# compose several models instead of returning one model serializer. This keeps
# the generated TS client typed without changing runtime behaviour.
RegisterResponse = inline_serializer(
    name="RegisterResponse",
    fields={
        "user": UserSerializer(),
        "clinic": ClinicSerializer(),
        "membership": MembershipSerializer(),
    },
)

LoginResponse = inline_serializer(
    name="LoginResponse",
    fields={
        "user": UserSerializer(),
        "memberships": MembershipWithClinicSerializer(many=True),
        "clinics": ClinicSerializer(many=True),
    },
)

MeResponse = inline_serializer(
    name="MeResponse",
    fields={
        "user": UserSerializer(),
        "memberships": MembershipWithClinicSerializer(many=True),
        "current_clinic": ClinicSerializer(allow_null=True),
    },
)

MfaEnableResponse = inline_serializer(
    name="MfaEnableResponse",
    fields={
        "secret": drf_serializers.CharField(),
        "provisioning_uri": drf_serializers.CharField(),
    },
)

PasswordResetAcceptedResponse = inline_serializer(
    name="PasswordResetAcceptedResponse",
    fields={"status": drf_serializers.CharField()},
)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    """Set the double-submit CSRF cookie and return no content."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(responses={204: None})
    def get(self, request):
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(csrf_protect, name="dispatch")
class RegisterView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(request=RegisterSerializer, responses={201: RegisterResponse})
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user, clinic, membership = register_clinic_owner(
            clinic_name=data["clinic_name"],
            clinic_slug=data["clinic_slug"],
            timezone_name=data["timezone"],
            currency=data["currency"],
            full_name=data["full_name"],
            email=data["email"],
            password_value=data["password"],
        )
        # Registration logs the new owner in, exactly like a successful login.
        login_user(request, user)

        return Response(
            {
                "user": UserSerializer(user).data,
                "clinic": ClinicSerializer(clinic).data,
                "membership": MembershipSerializer(membership).data,
            },
            status=status.HTTP_201_CREATED,
        )


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    @extend_schema(request=LoginSerializer, responses=LoginResponse)
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = authenticate_staff(
            request,
            email=data["email"],
            password_value=data["password"],
            otp=data.get("otp") or None,
        )
        memberships = list(active_memberships(user))
        return Response(
            {
                "user": UserSerializer(user).data,
                "memberships": MembershipWithClinicSerializer(memberships, many=True).data,
                "clinics": [ClinicSerializer(m.clinic).data for m in memberships],
            }
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        user = request.user
        clinic = getattr(request, "clinic", None)
        clinic_id = getattr(clinic, "id", None) or resolve_single_clinic(user)
        with transaction.atomic():
            logout_user(request)
            if clinic_id is not None:
                record_audit(
                    "auth.logout",
                    actor=user,
                    clinic=clinic_id,
                    request=request,
                    entity_type="accounts.user",
                    entity_id=str(user.pk),
                )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=MeResponse)
    def get(self, request):
        memberships = list(active_memberships(request.user))
        current_clinic = getattr(request, "clinic", None)
        return Response(
            {
                "user": UserSerializer(request.user).data,
                "memberships": MembershipWithClinicSerializer(memberships, many=True).data,
                "current_clinic": (
                    ClinicSerializer(current_clinic).data if current_clinic is not None else None
                ),
            }
        )


@method_decorator(csrf_protect, name="dispatch")
class PasswordResetView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    @extend_schema(
        request=PasswordResetSerializer,
        responses={202: PasswordResetAcceptedResponse},
    )
    def post(self, request):
        serializer = PasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request_password_reset(serializer.validated_data["email"], request=request)
        # Always the same response so accounts cannot be enumerated.
        return Response(
            {"status": "accepted"},
            status=status.HTTP_202_ACCEPTED,
        )


@method_decorator(csrf_protect, name="dispatch")
class PasswordConfirmView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(request=PasswordConfirmSerializer, responses={204: None})
    def post(self, request):
        serializer = PasswordConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        confirm_password_reset(
            serializer.validated_data["token"],
            serializer.validated_data["new_password"],
            request=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MfaEnableView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: MfaEnableResponse})
    def post(self, request):
        if request.user.mfa_enabled:
            raise ApiError(
                "mfa_already_enabled",
                "Multi-factor authentication is already enabled.",
                status_code=409,
            )

        secret = generate_secret()
        request.user.mfa_secret_enc = encrypt_secret(secret)
        request.user.save(update_fields=["mfa_secret_enc"])

        return Response(
            {
                "secret": secret,
                "provisioning_uri": provisioning_uri(
                    secret,
                    account_name=request.user.email,
                ),
            }
        )


class MfaVerifyView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=MfaVerifySerializer, responses={204: None})
    def post(self, request):
        serializer = MfaVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if request.user.mfa_enabled:
            raise ApiError(
                "mfa_already_enabled",
                "Multi-factor authentication is already enabled.",
                status_code=409,
            )
        if not request.user.mfa_secret_enc:
            raise ApiError(
                "mfa_not_started",
                "Start MFA enrollment before verifying a code.",
                status_code=400,
            )

        secret = decrypt_secret(request.user.mfa_secret_enc)
        if not verify_totp(secret, serializer.validated_data["otp"]):
            raise ApiError(
                "invalid_otp",
                "The one-time code is invalid.",
                status_code=401,
            )

        request.user.mfa_enabled = True
        request.user.save(update_fields=["mfa_enabled"])
        return Response(status=status.HTTP_204_NO_CONTENT)
