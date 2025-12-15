from django.urls import path
from . import views
from .views import (
    # clients
    ClientListView,
    ClientCreateView,
    ClientDetailView,
    ClientUpdateView,
    client_unapplied_payments,

    # time
    TimeEntryListView,
    TimeEntryCreateView,
    TimeEntryUpdateView,
    timeentry_client_entries,

    # expenses
    ExpenseListView,
    ExpenseCreateView,
    ExpenseDetailView,
    ExpenseUpdateView,
    ExpenseDeleteView,
    expense_client_entries,

    # invoices
    InvoiceListView,
    InvoiceCreateView,
    InvoiceUpdateView,
    InvoiceDeleteView,
    invoice_unbilled_fragment,
    invoice_print_view,
    invoice_print_pdf,

    # mobile PWA
    mobile_home,
    mobile_time_entry_create,
    mobile_expense_create,
    mobile_time_list,
    mobile_expense_list,
    mobile_meta,
)

from accounting.views import PaymentCreateForInvoiceView

app_name = "billing"

urlpatterns = [
    # clients
    path("clients/", ClientListView.as_view(), name="client_list"),
    path("clients/new/", ClientCreateView.as_view(), name="client_create"),
    path("clients/<int:pk>/", ClientDetailView.as_view(), name="client_detail"),
    path("clients/<int:pk>/edit/", ClientUpdateView.as_view(), name="client_edit"),
    path("clients/<int:pk>/unapplied-payments/", client_unapplied_payments, name="client_unapplied_payments"),

    # time entries
    path("time-entries/", TimeEntryListView.as_view(), name="timeentry_list"),
    path("time-entries/new/", TimeEntryCreateView.as_view(), name="timeentry_create"),
    path("time-entries/<int:pk>/edit/", TimeEntryUpdateView.as_view(), name="timeentry_edit"),
    path("time-entries/by-client/<int:client_id>/", timeentry_client_entries, name="timeentry_client_entries"),

    # expenses
    path("expenses/", ExpenseListView.as_view(), name="expense_list"),
    path("expenses/new/", ExpenseCreateView.as_view(), name="expense_create"),
    path("expenses/<int:pk>/", ExpenseDetailView.as_view(), name="expense_detail"),
    path("expenses/<int:pk>/edit/", ExpenseUpdateView.as_view(), name="expense_edit"),
    path("expenses/<int:pk>/delete/", ExpenseDeleteView.as_view(), name="expense_delete"),
    path("expenses/by-client/<int:client_id>/", expense_client_entries, name="expense_client_entries"),

    # invoices
    path("invoices/", views.InvoiceListView.as_view(), name="invoice_list"),
    path("invoices/create/", views.InvoiceCreateView.as_view(), name="invoice_create"),

    # invoice unbilled fragment
    path("invoices/unbilled-fragment/", views.invoice_unbilled_fragment,name="invoice_unbilled_fragment"),

    # View-only invoice page
    path("invoices/<int:pk>/", views.InvoiceDetailView.as_view(), name="invoice_detail"),

    # Edit (DRAFT only)
    path("invoices/<int:pk>/edit/", views.InvoiceUpdateView.as_view(), name="invoice_update"),

    # Status transitions
    path(
        "invoices/<int:pk>/status/<str:action>/",
        views.InvoiceChangeStatusView.as_view(),
        name="invoice_change_status",
    ),

    path("invoices/<int:pk>/print/", invoice_print_view, name="invoice_print"),
    path("invoices/<int:pk>/delete/", InvoiceDeleteView.as_view(), name="invoice_delete"),
    path("invoices/<int:pk>/print/", invoice_print_view, name="invoice_print"),
    path("invoices/<int:pk>/print-pdf/", invoice_print_pdf, name="invoice_print_pdf"),
    path("invoices/<int:invoice_id>/apply-payment/", PaymentCreateForInvoiceView.as_view(), name="payment_apply_invoice"),

    # mobile PWA
    path("m/", mobile_home, name="mobile_home"),
    path("m/api/time-entries/", mobile_time_entry_create, name="mobile_time_entry_create"),
    path("m/api/expenses/", mobile_expense_create, name="mobile_expense_create"),
    path("m/api/meta/", mobile_meta, name="mobile_meta"),
    path("m/time/", mobile_time_list, name="mobile_time_list"),
    path("m/expenses/", mobile_expense_list, name="mobile_expense_list"),

]
