"""Minimal user model contract."""

from __future__ import annotations

import uuid

import pytest
from django.db import IntegrityError, models, transaction

from apps.accounts.models import User

pytestmark = pytest.mark.django_db


def test_user_has_uuid7_primary_key():
    user = User.objects.create_user(email="doctor@example.com", password="s3cret-pass")

    assert isinstance(user.pk, uuid.UUID)
    assert user.pk.version == 7
    assert isinstance(User._meta.pk, models.UUIDField)
    assert User._meta.pk.primary_key is True
    assert User._meta.db_table == "accounts_user"


def test_email_is_normalized_and_used_as_username():
    user = User.objects.create_user(
        email="Doctor@Example.COM",
        password="s3cret-pass",
        full_name="Dr Example",
    )

    assert user.email == "doctor@example.com"
    assert user.get_username() == "doctor@example.com"
    assert User.USERNAME_FIELD == "email"
    assert user.check_password("s3cret-pass")
    assert user.is_active is True
    assert user.is_staff is False
    assert user.is_superuser is False


def test_create_superuser_sets_flags():
    admin = User.objects.create_superuser(
        email="owner@example.com",
        password="s3cret-pass",
        full_name="Owner",
    )

    assert admin.is_staff is True
    assert admin.is_superuser is True
    assert admin.is_active is True


def test_email_is_required():
    with pytest.raises(ValueError):
        User.objects.create_user(email="", password="s3cret-pass")


def test_email_uniqueness_is_case_insensitive_at_the_database_level():
    # bulk_create bypasses User.save(), proving the constraint itself is
    # case-insensitive rather than relying on application-level normalization.
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            User.objects.bulk_create(
                [
                    User(email="Owner@Example.com", password="x"),
                    User(email="owner@example.com", password="y"),
                ]
            )

    assert User.objects.count() == 0


def test_direct_save_normalizes_email():
    user = User(email="  MiXeD@Example.COM  ")
    user.set_password("s3cret-pass")
    user.save()

    user.refresh_from_db()
    assert user.email == "mixed@example.com"


def test_get_by_natural_key_is_case_insensitive(user_factory):
    user = user_factory("doctor@example.com")

    assert User.objects.get_by_natural_key("Doctor@Example.COM") == user


def test_superuser_email_is_normalized():
    admin = User.objects.create_superuser(
        email="Admin@Example.COM",
        password="s3cret-pass",
    )

    assert admin.email == "admin@example.com"
    assert admin.is_staff is True
    assert admin.is_superuser is True
