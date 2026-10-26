"""Root URL configuration.

Health endpoints stay at the root (no version prefix); the versioned API lives
under ``/api/v1/``.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("apps.core.urls")),
    path("api/v1/", include("config.api_urls")),
    path("admin/", admin.site.urls),
]

# Keep JSON clients on the same envelope even when routing or middleware fails
# before a DRF view runs (DEBUG=false only, which is every deployed environment).
handler404 = "apps.core.errors.api_not_found"
handler500 = "apps.core.errors.api_server_error"
