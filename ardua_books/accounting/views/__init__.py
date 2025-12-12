"""
Accounting views package.

Re-exports all views for backward compatibility with existing imports.
"""

from accounting.views.reports_views import (
    ReportsHomeView,
    TrialBalanceView, 
    IncomeStatementView, 
    ARAgingView, 
    ClientBalanceSummaryView,
)
from accounting.views.journal_views import (
    JournalEntryListView,
    JournalEntryDetailView,
)

from accounting.views.payment_views import (
    PaymentListView,
    PaymentDetailView,
    PaymentCreateGeneralView,
    PaymentCreateFromTransactionView,
    PaymentCreateForInvoiceView,
    payment_invoice_fragment,
)

from accounting.views.bank_views import (
    BankAccountListView,
    BankAccountCreateView,
    BankAccountDetailView,
    BankTransactionCreateView,
    BankTransactionListView,
    BankTransactionListForAccountView,
    OffsetAccountFilterView,
    BankRegisterView,
    BankTransactionDetailView,
    BankTransactionCSVImportView,
    BankTransactionLinkPaymentView,
    BankTransactionMarkOwnerEquityView,
)

__all__ = [
    # Journal views
    "JournalEntryListView",
    "JournalEntryDetailView",
    # Payment views
    "PaymentListView",
    "PaymentDetailView",
    "PaymentCreateGeneralView",
    "PaymentCreateFromTransactionView",
    "PaymentCreateForInvoiceView",
    "payment_invoice_fragment",
    # Bank views
    "BankAccountListView",
    "BankAccountCreateView",
    "BankAccountDetailView",
    "BankTransactionCreateView",
    "BankTransactionListView",
    "BankTransactionListForAccountView",
    "OffsetAccountFilterView",
    "BankRegisterView",
    "BankTransactionDetailView",
    "BankTransactionCSVImportView",
    "BankTransactionLinkPaymentView",
    "BankTransactionMarkOwnerEquityView",
    # Report views
    "ReportsHomeView",
    "TrialBalanceView", 
    "IncomeStatementView", 
    "ARAgingView", 
    "ClientBalanceSummaryView",
]
