"""
Billing views package.

Re-exports all views for backward compatibility with existing imports.
"""
from billing.views.client_views import (
    ClientListView,
    ClientCreateView,
    ClientDetailView,
    ClientUpdateView,
    ClientFinancialView,
)

from billing.views.time_entry_views import (
    TimeEntryListView,
    TimeEntryCreateView,
    TimeEntryUpdateView,
)

from billing.views.expense_views import (
    ExpenseListView,
    ExpenseCreateView,
    ExpenseDetailView,
    ExpenseUpdateView,
    ExpenseDeleteView,
)

from billing.views.invoice_views import (
    InvoiceListView,
    InvoiceDetailView,
    InvoiceCreateView,
    InvoiceUpdateView,
    InvoiceChangeStatusView,
    InvoiceDeleteView,
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
    "ClientFinancialView",
    # Time entry views
    "TimeEntryListView",
    "TimeEntryCreateView",
    "TimeEntryUpdateView",
    # Expense views
    "ExpenseListView",
    "ExpenseCreateView",
    "ExpenseDetailView",
    "ExpenseUpdateView",
    "ExpenseDeleteView",
    # Invoice views
    "InvoiceListView",
    "InvoiceDetailView",
    "InvoiceCreateView",
    "InvoiceUpdateView",
    "InvoiceChangeStatusView",
    "InvoiceDeleteView",
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
