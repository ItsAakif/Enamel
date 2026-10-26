"""Portal URL routes (mounted under ``/api/v1/``)."""

from django.urls import path

from .views import (
    PortalAppointmentCancelView,
    PortalAppointmentDetailView,
    PortalAppointmentRescheduleView,
    PortalAppointmentsView,
    PortalDocumentDownloadView,
    PortalDocumentsView,
    PortalInvoiceDetailView,
    PortalInvoiceReceiptView,
    PortalInvoicesView,
    PortalLogoutView,
    PortalMeView,
    PortalPlanDetailView,
    PortalPlanItemActionView,
    PortalPlansView,
    PortalRecordsView,
    PortalRequestCodeView,
    PortalVerifyView,
)

app_name = "portal"

urlpatterns = [
    path("portal/auth/request-code/", PortalRequestCodeView.as_view(), name="auth-request-code"),
    path("portal/auth/verify/", PortalVerifyView.as_view(), name="auth-verify"),
    path("portal/auth/logout/", PortalLogoutView.as_view(), name="auth-logout"),
    path("portal/me/", PortalMeView.as_view(), name="me"),
    path("portal/records/", PortalRecordsView.as_view(), name="records"),
    path("portal/appointments/", PortalAppointmentsView.as_view(), name="appointments"),
    path(
        "portal/appointments/<uuid:appointment_id>/",
        PortalAppointmentDetailView.as_view(),
        name="appointment-detail",
    ),
    path(
        "portal/appointments/<uuid:appointment_id>/cancel/",
        PortalAppointmentCancelView.as_view(),
        name="appointment-cancel",
    ),
    path(
        "portal/appointments/<uuid:appointment_id>/reschedule/",
        PortalAppointmentRescheduleView.as_view(),
        name="appointment-reschedule",
    ),
    path("portal/plans/", PortalPlansView.as_view(), name="plans"),
    path("portal/plans/<uuid:plan_id>/", PortalPlanDetailView.as_view(), name="plan-detail"),
    path(
        "portal/plans/<uuid:plan_id>/items/<uuid:item_id>/<str:action>/",
        PortalPlanItemActionView.as_view(),
        name="plan-item-action",
    ),
    path("portal/invoices/", PortalInvoicesView.as_view(), name="invoices"),
    path(
        "portal/invoices/<uuid:invoice_id>/",
        PortalInvoiceDetailView.as_view(),
        name="invoice-detail",
    ),
    path(
        "portal/invoices/<uuid:invoice_id>/receipt/",
        PortalInvoiceReceiptView.as_view(),
        name="invoice-receipt",
    ),
    path("portal/documents/", PortalDocumentsView.as_view(), name="documents"),
    path(
        "portal/documents/<uuid:document_id>/download/",
        PortalDocumentDownloadView.as_view(),
        name="document-download",
    ),
]
