"""Clinic permission enforcement.

``require_perm`` is the service-layer helper (Celery tasks, AI tools, portal)
and ``HasClinicPermission`` is the DRF view permission. Both fail closed:
foreign objects are reported as 404 by the view layer, permission failures are
403 with a stable code.
"""

from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission

from .errors import CODE_PERMISSION_DENIED, CODE_UNAUTHENTICATED, ApiError


def require_perm(user: Any, membership: Any, perm: str) -> bool:
    """Assert that ``membership`` grants ``perm`` to ``user``.

    Returns ``True`` on success and raises ``ApiError`` (403) otherwise. The
    membership must be active, belong to the user, and carry the permission
    directly from its role or through a grant-only override.
    """

    if (
        membership is None
        or user is None
        or not getattr(user, "is_authenticated", True)
        or membership.user_id != user.id
        or not membership.is_active
    ):
        raise ApiError(
            CODE_PERMISSION_DENIED,
            "You do not have permission to perform this action.",
            status_code=403,
        )

    permissions = membership.effective_permissions()
    if perm not in permissions:
        raise ApiError(
            CODE_PERMISSION_DENIED,
            "You do not have permission to perform this action.",
            status_code=403,
        )
    return True


class HasClinicPermission(BasePermission):
    """Require an authenticated user with an active clinic membership.

    Views declare ``required_perms`` (or override ``get_required_perms``). The
    permission class resolves the clinic when the middleware did not, and
    raises ``clinic_required`` when the user belongs to several clinics without
    an ``X-Clinic-Id`` header.
    """

    def has_permission(self, request: Any, view: Any) -> bool:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            raise ApiError(
                CODE_UNAUTHENTICATED,
                "Authentication is required.",
                status_code=401,
            )
        if not user.is_active:
            raise ApiError(
                CODE_UNAUTHENTICATED,
                "Authentication is required.",
                status_code=401,
            )

        scope_error = getattr(request, "clinic_scope_error", None)
        if scope_error is not None:
            raise scope_error

        if getattr(request, "clinic", None) is None:
            if getattr(request, "clinic_required", False):
                raise ApiError(
                    "clinic_required",
                    "Select a clinic with the X-Clinic-Id header.",
                    status_code=400,
                )
            raise ApiError(
                CODE_PERMISSION_DENIED,
                "You do not have permission to perform this action.",
                status_code=403,
            )

        get_required = getattr(view, "get_required_perms", None)
        required = get_required(request) if callable(get_required) else getattr(
            view, "required_perms", ()
        )
        for perm in required or ():
            require_perm(user, getattr(request, "membership", None), perm)
        return True

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        clinic = getattr(request, "clinic", None)
        if clinic is None or getattr(obj, "clinic_id", None) != clinic.id:
            # Foreign (or deleted) objects are always 404 so IDs cannot be
            # probed across tenants.
            raise ApiError("not_found", "Not found.", status_code=404)

        get_required = getattr(view, "get_required_perms", None)
        required = get_required(request) if callable(get_required) else getattr(
            view, "required_perms", ()
        )
        for perm in required or ():
            require_perm(request.user, getattr(request, "membership", None), perm)
        return True
