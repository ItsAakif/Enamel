"""Health, readiness and OpenAPI schema endpoints.

Health endpoints are unauthenticated and never expose PHI, secrets, or internal
hostnames. ``/api/v1/schema/`` serves the generated OpenAPI 3.1 document that
the frontend client is generated from.
"""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET
from drf_spectacular.renderers import OpenApiJsonRenderer
from drf_spectacular.views import SpectacularAPIView
from rest_framework.permissions import AllowAny

from .health import run_readiness_checks

SERVICE_NAME = "enamel-api"


class ApiSchemaJsonRenderer(OpenApiJsonRenderer):
    """Plain ``application/json`` for the committed contract endpoint.

    drf-spectacular defaults to ``application/vnd.oai.openapi+json``; the
    project contract (and every browser/client probe) asks for ordinary JSON.
    """

    media_type = "application/json"


class ApiSchemaView(SpectacularAPIView):
    """Serve the OpenAPI schema as JSON (no auth, no HTML renderer)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    renderer_classes = [ApiSchemaJsonRenderer]


@require_GET
def healthz(request: HttpRequest) -> JsonResponse:
    response = JsonResponse({"status": "ok", "service": SERVICE_NAME})
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def readyz(request: HttpRequest) -> JsonResponse:
    ok, checks = run_readiness_checks()
    response = JsonResponse(
        {"status": "ok" if ok else "error", "checks": checks},
        status=200 if ok else 503,
    )
    response["Cache-Control"] = "no-store"
    return response
