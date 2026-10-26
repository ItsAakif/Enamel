"""Public booking URL routes (mounted under /api/v1/)."""

from django.urls import path

from .views import (
    PublicAvailabilityView,
    PublicBookingCreateView,
    PublicBookingVerifyView,
    PublicClinicView,
    PublicLinkView,
)

app_name = "booking"

urlpatterns = [
    path(
        "public/clinics/<slug:slug>/",
        PublicClinicView.as_view(),
        name="public-clinic",
    ),
    path(
        "public/clinics/<slug:slug>/availability/",
        PublicAvailabilityView.as_view(),
        name="public-availability",
    ),
    path(
        "public/bookings/",
        PublicBookingCreateView.as_view(),
        name="public-booking-create",
    ),
    path(
        "public/bookings/<str:reference>/verify/",
        PublicBookingVerifyView.as_view(),
        name="public-booking-verify",
    ),
    path(
        "public/links/<str:token>/",
        PublicLinkView.as_view(),
        name="public-link",
    ),
]
