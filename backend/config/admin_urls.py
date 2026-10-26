"""The platform-admin URL surface (ADR-013, decision D-1).

The platform admin is a **separately permissioned service**: its own compose
service, its own credential and its own port, and the public web service does
not proxy it. It serves the Django admin and **nothing else** - the
clinic-facing API is not reachable on that port, and the ordinary clinic
administration inside the api service stays tenant-scoped exactly as it is
today.

The service runs ``config.admin_wsgi`` (which points the root URLconf at this
module) and the compose wiring gives it the platform-admin database role.
"""

from __future__ import annotations

from django.contrib import admin
from django.http import JsonResponse
from django.urls import path


def healthz(request):  # noqa: ANN001, ANN201 - the Django view signature
    """The liveness probe of the admin service."""

    return JsonResponse({"status": "ok", "surface": "platform-admin"})


urlpatterns = [
    path("healthz", healthz, name="admin-healthz"),
    path("admin/", admin.site.urls),
]
