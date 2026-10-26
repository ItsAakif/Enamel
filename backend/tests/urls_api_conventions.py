"""Test-only URLconf for the T102 API contract suite.

Mounted with ``@pytest.mark.urls("tests.urls_api_conventions")`` so the probes
never appear in the committed schema or in production routing.
"""

from __future__ import annotations

from django.http import Http404
from django.urls import path
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.clinics.models import Location
from apps.core.errors import ApiError
from apps.core.pagination import StandardPagination
from apps.core.throttles import AiThrottle, AuthThrottle, ExportThrottle, PublicBookingThrottle
from tests.views_api_conventions import IdempotentLocationCreateView


class ProbeSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=64)


class ValidationProbeView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ProbeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response({"ok": True})


class AuthRequiredProbeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"ok": True})


class DenyAll(BasePermission):
    def has_permission(self, request, view):
        return False


class PermissionProbeView(APIView):
    # No authenticators: an anonymous caller then reaches the permission check
    # and gets 403 rather than being retried as an unauthenticated 401.
    authentication_classes: list = []
    permission_classes = [DenyAll]

    def get(self, request):
        return Response({"ok": True})


class NotFoundProbeView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        raise Http404("probe-missing")


class ConflictProbeView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        raise ApiError(
            "conflict",
            "The request conflicts with the current state.",
            status_code=status.HTTP_409_CONFLICT,
        )


class ServerErrorProbeView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        raise RuntimeError("boom-secret-message")


class AuthThrottleProbeView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    def get(self, request):
        return Response({"ok": True})


class PublicBookingThrottleProbeView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicBookingThrottle]

    def post(self, request):
        return Response({"ok": True})


class AiThrottleProbeView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [AiThrottle]

    def get(self, request):
        return Response({"ok": True})


class ExportThrottleProbeView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ExportThrottle]

    def get(self, request):
        return Response({"ok": True})


class PaginatedLocationView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    allowed_query_params = {"clinic", "page", "page_size", "search", "ordering"}

    def get(self, request):
        queryset = Location.all_objects.all()
        clinic_id = request.query_params.get("clinic")
        if clinic_id:
            queryset = queryset.filter(clinic_id=clinic_id)
        search = (request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(name__icontains=search)
        ordering = request.query_params.get("ordering") or "name"
        if ordering not in {"name", "-name", "created_at", "-created_at"}:
            ordering = "name"
        queryset = queryset.order_by(ordering, "id")

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        rows = [{"id": str(row.id), "name": row.name} for row in page]
        return paginator.get_paginated_response(rows)


class RequiredIdempotencyLocationView(IdempotentLocationCreateView):
    idempotency_required = True


urlpatterns = [
    path("probe/validation/", ValidationProbeView.as_view(), name="probe-validation"),
    path("probe/auth/", AuthRequiredProbeView.as_view(), name="probe-auth"),
    path("probe/permission/", PermissionProbeView.as_view(), name="probe-permission"),
    path("probe/not-found/", NotFoundProbeView.as_view(), name="probe-not-found"),
    path("probe/conflict/", ConflictProbeView.as_view(), name="probe-conflict"),
    path("probe/server-error/", ServerErrorProbeView.as_view(), name="probe-server-error"),
    path("probe/auth-throttle/", AuthThrottleProbeView.as_view(), name="probe-auth-throttle"),
    path(
        "probe/public-booking/",
        PublicBookingThrottleProbeView.as_view(),
        name="probe-public-booking",
    ),
    path("probe/ai-throttle/", AiThrottleProbeView.as_view(), name="probe-ai-throttle"),
    path("probe/export-throttle/", ExportThrottleProbeView.as_view(), name="probe-export-throttle"),
    path("probe/locations/", PaginatedLocationView.as_view(), name="probe-locations"),
    path(
        "probe/locations/idempotent/",
        IdempotentLocationCreateView.as_view(),
        name="probe-idempotent-locations",
    ),
    path(
        "probe/locations/idempotent-required/",
        RequiredIdempotencyLocationView.as_view(),
        name="probe-idempotent-required",
    ),
]

handler404 = "apps.core.errors.api_not_found"
handler500 = "apps.core.errors.api_server_error"
