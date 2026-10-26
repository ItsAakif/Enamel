"""Test-only views exercising the API contract primitives in T102.

These are not application endpoints. They live under ``backend/tests`` and are
mounted only by ``tests.urls_api_conventions`` via ``override_settings``.
"""

from __future__ import annotations

import time

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.clinics.models import Location
from apps.core.errors import ApiError
from apps.core.idempotency import IdempotencyMixin
from apps.core.pagination import StandardPagination
from apps.core.throttles import PublicBookingThrottle

STATUS_BY_CODE = {
    "validation_error": 400,
    "unauthenticated": 401,
    "session_expired": 401,
    "permission_denied": 403,
    "not_found": 404,
    "conflict": 409,
    "slot_conflict": 409,
    "version_conflict": 409,
    "payload_too_large": 413,
    "throttled": 429,
    "provider_unavailable": 503,
    "idempotency_mismatch": 409,
    "invalid_page": 400,
}


class ErrorProbeView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request, code):
        status_code = STATUS_BY_CODE[code]
        details = {"retry_after": 12} if code == "throttled" else {}
        raise ApiError(
            code,
            f"Probe {code}.",
            status_code=status_code,
            field_errors={"probe": ["invalid"]} if status_code in (400, 409) else {},
            details=details,
        )


class BoomView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        raise RuntimeError("boom-secret-message")


class PaginatedLocationListView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    allowed_query_params = {"clinic"}

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
        payload = [{"id": str(row.id), "name": row.name} for row in page]
        return paginator.get_paginated_response(payload)


class IdempotentLocationCreateView(IdempotencyMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        clinic = getattr(request, "clinic", None)
        if clinic is None:
            raise ApiError(
                "clinic_required",
                "Select a clinic with the X-Clinic-Id header.",
                status_code=400,
            )
        # Concurrency tests widen the window between the advisory lock and the
        # side effect so a second request is guaranteed to queue behind it.
        delay = request.query_params.get("delay")
        if delay:
            time.sleep(min(float(delay), 2.0))
        location = Location.all_objects.create(
            clinic=clinic,
            name=str(request.data.get("name") or "Probe location"),
        )
        return Response(
            {"id": str(location.id), "name": location.name},
            status=status.HTTP_201_CREATED,
        )


class ThrottledBookingProbeView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicBookingThrottle]

    def post(self, request):
        return Response({"ok": True})
