"""Portal permissions (T121)."""

from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission

from apps.core.errors import CODE_UNAUTHENTICATED, ApiError


class IsPortalAuthenticated(BasePermission):
    """Require a resolved portal identity.

    A missing/invalid portal cookie is a 401 ``unauthenticated``. The staff
    session cookie is irrelevant here because the view declares only
    :class:`~apps.portal.authentication.PatientSessionAuthentication`.
    """

    def has_permission(self, request: Any, view: Any) -> bool:
        if getattr(request, "identity", None) is None:
            raise ApiError(
                CODE_UNAUTHENTICATED,
                "Authentication is required.",
                status_code=401,
            )
        return True


__all__ = ["IsPortalAuthenticated"]
