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

from accounting.views.report_exports import (
    trial_balance_print,
    trial_balance_pdf,
    trial_balance_csv,
    income_statement_print,
    income_statement_pdf,
    income_statement_csv,
    client_balance_print,
    client_balance_pdf,
    client_balance_csv,
    journal_entries_print,
    journal_entries_pdf,
    journal_entries_csv,
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
    # Report exports
    "trial_balance_print",
    "trial_balance_pdf",
    "trial_balance_csv",
    "income_statement_print",
    "income_statement_pdf",
    "income_statement_csv",
    "client_balance_print",
    "client_balance_pdf",
    "client_balance_csv",
    "journal_entries_print",
    "journal_entries_pdf",
    "journal_entries_csv",
]
