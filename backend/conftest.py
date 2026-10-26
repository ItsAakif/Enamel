"""Shared pytest fixtures for the backend test suite."""

from __future__ import annotations

from unittest import mock

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    """Keep DRF throttle counters isolated between tests."""

    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def tenant_write_context():
    """Give a scope-less tenant write its own clinic context.

    The test fixtures create tenant rows with an explicit ``clinic=`` and
    without a clinic scope. The application never does that: every service
    writes inside ``with_clinic(...)``, and the ORM refuses a scope-less
    query. The database is stricter still - once the suite runs as a
    non-superuser role (ADR-013 step A8), a write without a clinic context
    is refused by the row-level policies.

    The harness therefore opens the context for the write itself. The
    context is transaction-local and is closed again immediately, and the
    write is still checked against the policies of the row's own clinic.
    """

    from django.db import models

    from apps.core.rls import rls_context
    from apps.core.tenancy import current_scope

    original = models.Model.save

    def save(self, *args, **kwargs):
        clinic_id = getattr(self, "clinic_id", None)
        if clinic_id is not None and current_scope() is None:
            with rls_context(clinic_id):
                return original(self, *args, **kwargs)
        return original(self, *args, **kwargs)

    with mock.patch.object(models.Model, "save", save):
        yield


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def user_factory(db):
    from apps.accounts.models import User

    def _create(email: str, password: str = "Str0ngPass!2026", **extra):
        extra.setdefault("full_name", email.split("@")[0].title())
        return User.objects.create_user(email=email, password=password, **extra)

    return _create


@pytest.fixture
def clinic_factory(db):
    from apps.clinics.models import Clinic

    def _create(slug: str = "clinic-a", name: str | None = None, **extra):
        return Clinic.objects.create(
            name=name or f"Clinic {slug}",
            slug=slug,
            timezone="Asia/Kolkata",
            currency="INR",
            **extra,
        )

    return _create


@pytest.fixture
def membership_factory(db):
    from apps.clinics.models import Membership
    from apps.clinics.services.members import create_membership

    def _create(clinic, user, role: str = Membership.Role.OWNER, **extra):
        return create_membership(clinic, user, role=role, **extra)

    return _create
