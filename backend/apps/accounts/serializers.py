"""Serializers for accounts and authentication endpoints."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.accounts.models import User
from apps.clinics.models import Clinic


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "full_name",
            "phone",
            "mfa_enabled",
            "is_active",
            "date_joined",
            "last_login",
        ]
        read_only_fields = fields


class RegisterSerializer(serializers.Serializer):
    clinic_name = serializers.CharField(max_length=255)
    clinic_slug = serializers.SlugField(max_length=64)
    timezone = serializers.CharField(max_length=64, default="Asia/Kolkata")
    currency = serializers.RegexField(r"^[A-Za-z]{3}$", default="INR")
    full_name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_email(self, value: str) -> str:
        normalized = value.strip().lower()
        if User.objects.filter(email=normalized).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return normalized

    def validate_clinic_slug(self, value: str) -> str:
        normalized = value.strip().lower()
        if Clinic.objects.filter(slug=normalized).exists():
            raise serializers.ValidationError("A clinic with this slug already exists.")
        return normalized

    def validate_timezone(self, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise serializers.ValidationError("Unknown timezone.") from None
        return value

    def validate_password(self, value: str) -> str:
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from None
        return value


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    otp = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=16,
    )


class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordConfirmSerializer(serializers.Serializer):
    token = serializers.CharField(trim_whitespace=True)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)


class MfaVerifySerializer(serializers.Serializer):
    otp = serializers.CharField(max_length=16)
