"""Core URL routes (mounted at the site root)."""

from django.urls import path

from .views import healthz, readyz

app_name = "core"

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
]
