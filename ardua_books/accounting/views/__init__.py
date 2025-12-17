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
    BankTransactionCreateView,
    BankTransactionListView,
    BankTransactionListForAccountView,
    OffsetAccountFilterView,
    BankRegisterView,
    BankTransactionDetailView,
    BankTransactionCSVImportView,
    BankTransactionLinkPaymentView,
    BankTransactionMarkOwnerEquityView,
    banktransaction_link_expense,
    banktransaction_match_transfer,
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
    "BankTransactionCreateView",
    "BankTransactionListView",
    "BankTransactionListForAccountView",
    "OffsetAccountFilterView",
    "BankRegisterView",
    "BankTransactionDetailView",
    "BankTransactionCSVImportView",
    "BankTransactionLinkPaymentView",
    "BankTransactionMarkOwnerEquityView",
    "banktransaction_link_expense",
    "banktransaction_match_transfer",
    # Report views
    "ReportsHomeView",
    "TrialBalanceView", 
    "IncomeStatementView", 
    "ARAgingView", 
    "ClientBalanceSummaryView",
]
