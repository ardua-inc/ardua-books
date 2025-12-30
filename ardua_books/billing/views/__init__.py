"""
Billing views package.

Re-exports all views for backward compatibility with existing imports.
"""
from billing.views.client_views import (
    ClientListView,
    ClientCreateView,
    ClientDetailView,
    ClientUpdateView,
    client_unapplied_payments,
)

from billing.views.time_entry_views import (
    TimeEntryListView,
    TimeEntryCreateView,
    TimeEntryUpdateView,
    timeentry_client_entries,
)

from billing.views.expense_views import (
    ExpenseListView,
    ExpenseCreateView,
    ExpenseDetailView,
    ExpenseUpdateView,
    ExpenseDeleteView,
    expense_client_entries,
)

from billing.views.invoice_views import (
    InvoiceListView,
    InvoiceDetailView,
    InvoiceCreateView,
    InvoiceUpdateView,
    InvoiceChangeStatusView,
    InvoiceDeleteView,
    invoice_email_view,
)

from billing.views.pdf_views import (
    invoice_print_view,
    invoice_print_pdf,
)

from billing.views.mobile_views import (
    mobile_home,
    mobile_time_list,
    mobile_expense_list,
    mobile_time_entry_create,
    mobile_expense_create,
    mobile_meta,
)

from billing.views.fragment_views import (
    invoice_unbilled_fragment,
)

__all__ = [
    # Client views
    "ClientListView",
    "ClientCreateView",
    "ClientDetailView",
    "ClientUpdateView",
    "client_unapplied_payments",
    # Time entry views
    "TimeEntryListView",
    "TimeEntryCreateView",
    "TimeEntryUpdateView",
    "timeentry_client_entries",
    # Expense views
    "ExpenseListView",
    "ExpenseCreateView",
    "ExpenseDetailView",
    "ExpenseUpdateView",
    "ExpenseDeleteView",
    "expense_client_entries",
    # Invoice views
    "InvoiceListView",
    "InvoiceDetailView",
    "InvoiceCreateView",
    "InvoiceUpdateView",
    "InvoiceChangeStatusView",
    "InvoiceDeleteView",
    "invoice_email_view",
    # PDF views
    "invoice_print_view",
    "invoice_print_pdf",
    # Mobile views
    "mobile_home",
    "mobile_time_list",
    "mobile_expense_list",
    "mobile_time_entry_create",
    "mobile_expense_create",
    "mobile_meta",
    # Fragment views
    "invoice_unbilled_fragment",
]
