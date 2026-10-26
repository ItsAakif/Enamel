"""Shared DRF view base for the imaging API (T127, T129)."""

from __future__ import annotations

from typing import Any

from rest_framework.views import APIView

from apps.core.errors import ApiError
from apps.core.permissions import HasClinicPermission


class NoStoreMixin:
    """Imaging responses reference patients and must never be cached."""

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class ImagingScopedView(NoStoreMixin, APIView):
    permission_classes = [HasClinicPermission]

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("imaging.view",)

    def clinic(self, request) -> Any:
        clinic = getattr(request, "clinic", None)
        if clinic is None:
            scope_error = getattr(request, "clinic_scope_error", None)
            if scope_error is not None:
                raise scope_error
            raise ApiError(
                "permission_denied",
                "You do not have permission to perform this action.",
                status_code=403,
            )
        return clinic


__all__ = ["ImagingScopedView", "NoStoreMixin"]
