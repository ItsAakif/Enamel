"""Billing URL routes (mounted under /api/v1/)."""

from django.urls import path

from .views import (
    InvoiceDetailView,
    InvoiceFromPlanView,
    InvoiceListCreateView,
    InvoicePdfView,
    PaymentCreateView,
    PaymentWebhookView,
    ReconciliationListView,
    RefundCreateView,
)

app_name = "billing"

urlpatterns = [
    path("invoices/", InvoiceListCreateView.as_view(), name="invoice-list"),
    path(
        "invoices/from-plan/<uuid:plan_id>/",
        InvoiceFromPlanView.as_view(),
        name="invoice-from-plan",
    ),
    path(
        "invoices/<uuid:invoice_id>/",
        InvoiceDetailView.as_view(),
        name="invoice-detail",
    ),
    path(
        "invoices/<uuid:invoice_id>/pdf/",
        InvoicePdfView.as_view(),
        name="invoice-pdf",
    ),
    path(
        "invoices/<uuid:invoice_id>/payments/",
        PaymentCreateView.as_view(),
        name="invoice-payment",
    ),
    path(
        "payments/<uuid:payment_id>/refund/",
        RefundCreateView.as_view(),
        name="payment-refund",
    ),
    path(
        "webhooks/payments/<str:gateway>/",
        PaymentWebhookView.as_view(),
        name="payment-webhook",
    ),
    path(
        "billing/reconciliation/",
        ReconciliationListView.as_view(),
        name="billing-reconciliation",
    ),
]
