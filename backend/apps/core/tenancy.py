"""Tenant scoping primitives.

The four enforcement layers are described in ADR-006 (Tenancy and
permissions). This
module implements the first three:

* ``ClinicScope`` resolved from ``X-Clinic-Id`` and an active membership.
* ``TenantQuerySet`` / ``TenantManager``: default-deny access scoped by the
  current request/task clinic. ``all_objects`` is the explicit escape hatch.
* ``ClinicScopedModel.save()`` refuses to write a row outside the active scope
  and fills ``clinic_id`` from the scope when it is omitted.

The current scope is stored in a context variable so service code and Celery
tasks (``with_clinic``) do not have to thread a request object through.

These are the application layers. The **fourth** layer - PostgreSQL
row-level security - is enforced at runtime as well: the ``api`` service runs
as ``enamel_app`` and the ``worker``/``beat`` as ``enamel_worker`` (both
``NOSUPERUSER``/``NOBYPASSRLS``, without DDL/DCL rights, and not members of
the owner or of any bypass holder), so the principal-scoped policies of
ADR-013 narrow every query underneath this module. ``all_objects`` is the
explicit escape hatch of the application layer; it does not defeat the
database policies, which still apply.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.db import models

_current_scope: ContextVar[ClinicScope | None] = ContextVar("enamel_clinic_scope", default=None)


class UnscopedQueryError(RuntimeError):
    """Raised when a tenant model is queried without an explicit scope."""


class CrossClinicWriteError(RuntimeError):
    """Raised when a write would cross the active clinic boundary."""


def _as_clinic_id(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    clinic_id = getattr(value, "clinic_id", None)
    if clinic_id is not None:
        return clinic_id
    pk = getattr(value, "pk", None)
    if isinstance(pk, uuid.UUID):
        return pk
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ClinicScope:
    """The resolved clinic for a request or background task."""

    clinic_id: uuid.UUID
    clinic: Any | None = None
    membership: Any | None = None

    @classmethod
    def from_membership(cls, membership: Any) -> ClinicScope:
        return cls(
            clinic_id=membership.clinic_id,
            clinic=getattr(membership, "clinic", None),
            membership=membership,
        )

    @classmethod
    def from_request(cls, request: Any, *, required: bool = False) -> ClinicScope | None:
        """Resolve the scope for ``request``.

        ``X-Clinic-Id`` always wins. Without it a single active membership is
        selected automatically; with several memberships the scope is
        ambiguous and ``required=True`` raises ``clinic_required``. A header
        that points at a foreign or inactive clinic raises ``not_found`` so the
        caller cannot enumerate clinics.
        """

        from .errors import ApiError

        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return None

        Membership = apps.get_model("clinics", "Membership")
        active = Membership.all_objects.filter(user=user, status="active").select_related("clinic")
        header = request.headers.get("X-Clinic-Id")

        if header:
            try:
                clinic_id = uuid.UUID(str(header))
            except (TypeError, ValueError):
                raise ApiError("not_found", "Clinic not found.", status_code=404) from None
            membership = active.filter(clinic_id=clinic_id).first()
            if membership is not None:
                return cls.from_membership(membership)
            # A former member (suspended/removed) gets 403: the clinic is not
            # foreign to them, they just no longer have access. A user with no
            # membership at all gets 404 so clinic ids cannot be enumerated.
            if Membership.all_objects.filter(user=user, clinic_id=clinic_id).exists():
                raise ApiError(
                    "permission_denied",
                    "You do not have permission to perform this action.",
                    status_code=403,
                )
            raise ApiError("not_found", "Clinic not found.", status_code=404)

        memberships = list(active)
        if len(memberships) == 1:
            return cls.from_membership(memberships[0])
        if len(memberships) > 1 and required:
            raise ApiError(
                "clinic_required",
                "Select a clinic with the X-Clinic-Id header.",
                status_code=400,
            )
        return None


def current_scope() -> ClinicScope | None:
    return _current_scope.get()


def set_current_scope(scope: ClinicScope | None) -> Token:
    return _current_scope.set(scope)


def reset_current_scope(token: Token) -> None:
    _current_scope.reset(token)


@contextmanager
def with_clinic(clinic: Any) -> Iterator[ClinicScope]:
    """Run a block with an explicit clinic scope (services, Celery, tests).

    The same clinic is pushed into the PostgreSQL RLS GUC so raw ORM access
    inside the block is filtered by the database as well as by
    :class:`TenantManager`.
    """

    from .rls import rls_context

    clinic_id = _as_clinic_id(clinic)
    if clinic_id is None:
        raise UnscopedQueryError("with_clinic() requires a clinic or clinic id.")
    scope = ClinicScope(
        clinic_id=clinic_id,
        clinic=clinic if hasattr(clinic, "pk") and not isinstance(clinic, uuid.UUID) else None,
    )
    token = _current_scope.set(scope)
    try:
        with rls_context(clinic_id):
            yield scope
    finally:
        _current_scope.reset(token)


class TenantQuerySet(models.QuerySet):
    """QuerySet with an explicit ``for_clinic`` scoping entry point."""

    def for_clinic(self, scope: Any) -> TenantQuerySet:
        clinic_id = _as_clinic_id(scope)
        if clinic_id is None:
            raise UnscopedQueryError("for_clinic() requires a clinic scope.")
        return self.filter(clinic_id=clinic_id)


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """Default manager that refuses to run without a resolved clinic scope."""

    def get_queryset(self) -> TenantQuerySet:
        queryset = super().get_queryset()
        scope = current_scope()
        if scope is None:
            raise UnscopedQueryError(
                f"{self.model.__name__}.objects requires a clinic scope; "
                "use for_clinic(), with_clinic(), or all_objects explicitly."
            )
        return queryset.filter(clinic_id=scope.clinic_id)


class ClinicScopedModel(models.Model):
    """Abstract base for every table carrying a ``clinic_id`` discriminator."""

    clinic = models.ForeignKey(
        "clinics.Clinic",
        on_delete=models.CASCADE,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantManager()
    all_objects = models.Manager.from_queryset(TenantQuerySet)()

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        scope = current_scope()
        if self.clinic_id is None:
            if scope is None:
                raise UnscopedQueryError(
                    f"{type(self).__name__}.save() requires a clinic scope or explicit clinic."
                )
            self.clinic_id = scope.clinic_id
        elif scope is not None and self.clinic_id != scope.clinic_id:
            raise CrossClinicWriteError(
                f"Refusing to write {type(self).__name__} for another clinic."
            )
        super().save(*args, **kwargs)
