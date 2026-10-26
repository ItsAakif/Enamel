"""Versioned API URL composition (``/api/v1/``)."""

from django.urls import include, path

from apps.core.views import ApiSchemaView

urlpatterns = [
    path("schema/", ApiSchemaView.as_view(), name="schema"),
    path("auth/", include("apps.accounts.urls")),
    path("", include("apps.clinics.urls")),
    path("", include("apps.patients.urls")),
    path("", include("apps.charting.urls")),
    path("", include("apps.scheduling.urls")),
    path("", include("apps.messaging.urls")),
    path("", include("apps.booking.urls")),
    path("", include("apps.plans.urls")),
    path("", include("apps.billing.urls")),
    path("", include("apps.ai.urls")),
    path("", include("apps.ml.urls")),
    path("", include("apps.imaging.urls")),
    path("", include("apps.analytics.urls")),
    path("", include("apps.interop.urls")),
    path("", include("apps.marketing.urls")),
    path("", include("apps.rcm.urls")),
    path("", include("apps.portal.urls")),
]
