from django.urls import path

from .views import (
    BankAccountListView,
    BankAccountCreateView,
    BankRegisterView,
    BankTransactionListView,
    BankTransactionListForAccountView,
    BankTransactionCreateView,
    BankTransactionDetailView,
    BankTransactionLinkPaymentView,
    BankTransactionMarkOwnerEquityView,
    BankTransactionRecategorizeView,
    BankTransactionBulkRecategorizeView,
    BankTransactionUnmatchView,
    BankTransactionCSVImportView,
    banktransaction_link_expense,
    banktransaction_match_transfer,
    BatchMatchExpensesView,
    BatchMatchPaymentsView,
    JournalEntryListView,
    JournalEntryDetailView,
    ManualJournalEntryCreateView,
    JournalEntryReverseView,
    PaymentApplyUnappliedView,
    PaymentCreateGeneralView,
    PaymentCreateFromTransactionView,
    PaymentDetailView,
    PaymentListView,
    payment_invoice_fragment,
    OffsetAccountFilterView,
    ReportsHomeView,
    TrialBalanceView,
    IncomeStatementView,
    ARAgingView,
    ClientBalanceSummaryView,
    AccountDrilldownView,
    BankReconciliationScheduleView,
    TransactionRuleListView,
    TransactionRuleCreateView,
    TransactionRuleUpdateView,
    TransactionRuleDeleteView,
    # Report exports
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
    bank_reconciliation_print,
    bank_reconciliation_pdf,
    bank_reconciliation_csv,
)

app_name = "accounting"

urlpatterns = [
    path("", ReportsHomeView.as_view(), name="home"),
    path("trial-balance/", TrialBalanceView.as_view(), name="trial_balance"),
    path("trial-balance/print/", trial_balance_print, name="trial_balance_print"),
    path("trial-balance/pdf/", trial_balance_pdf, name="trial_balance_pdf"),
    path("trial-balance/csv/", trial_balance_csv, name="trial_balance_csv"),
    path("income-statement/", IncomeStatementView.as_view(), name="income_statement"),
    path("income-statement/print/", income_statement_print, name="income_statement_print"),
    path("income-statement/pdf/", income_statement_pdf, name="income_statement_pdf"),
    path("income-statement/csv/", income_statement_csv, name="income_statement_csv"),
    path("account/<int:pk>/drilldown/", AccountDrilldownView.as_view(), name="account_drilldown"),
    path("journal/", JournalEntryListView.as_view(), name="journal_list"),
    path("journal/print/", journal_entries_print, name="journal_entries_print"),
    path("journal/pdf/", journal_entries_pdf, name="journal_entries_pdf"),
    path("journal/csv/", journal_entries_csv, name="journal_entries_csv"),
    path("journal/new/", ManualJournalEntryCreateView.as_view(), name="journal_create"),
    path("journal/<int:pk>/", JournalEntryDetailView.as_view(), name="journal_detail"),
    path("journal/<int:pk>/reverse/", JournalEntryReverseView.as_view(), name="journal_reverse"),
    path("payments/<int:pk>/", PaymentDetailView.as_view(), name="payment_detail"),
    path("payments/", PaymentListView.as_view(), name="payment_list"),
    path("payments/create/", PaymentCreateGeneralView.as_view(), name="payment_create_general"),
    path("payments/<int:pk>/apply/", PaymentApplyUnappliedView.as_view(), name="payment_apply_unapplied"),
    path("payment/invoice-fragment/", payment_invoice_fragment, name="payment_invoice_fragment"),
    path("reports/ar-aging/", ARAgingView.as_view(), name="ar_aging"),
    path("reports/client-balances/", ClientBalanceSummaryView.as_view(), name="client_balance_summary"),
    path("reports/client-balances/print/", client_balance_print, name="client_balance_print"),
    path("reports/client-balances/pdf/", client_balance_pdf, name="client_balance_pdf"),
    path("reports/client-balances/csv/", client_balance_csv, name="client_balance_csv"),
    path("reports/bank-reconciliation/", BankReconciliationScheduleView.as_view(), name="bank_reconciliation_schedule"),
    path("reports/bank-reconciliation/print/", bank_reconciliation_print, name="bank_reconciliation_print"),
    path("reports/bank-reconciliation/pdf/", bank_reconciliation_pdf, name="bank_reconciliation_pdf"),
    path("reports/bank-reconciliation/csv/", bank_reconciliation_csv, name="bank_reconciliation_csv"),
    # BANK ACCOUNTS
    path("bank-accounts/", BankAccountListView.as_view(), name="bankaccount_list"),
    path("bank-accounts/new/", BankAccountCreateView.as_view(), name="bankaccount_create"),

    # TRANSACTIONS
    path(
        "bank-accounts/<int:account_id>/transactions/new/",
        BankTransactionCreateView.as_view(),
        name="banktransaction_create",
    ),
    path(
        "bank-accounts/<int:account_id>/transactions/",
        BankTransactionListForAccountView.as_view(),
        name="banktransaction_list_for_account",
    ),
    # Single Transaction Detail
    path(
        "bank-transactions/<int:pk>/",
        BankTransactionDetailView.as_view(),
        name="banktransaction_detail",
    ),
    path(
        "bank-accounts/<int:pk>/register/",
        BankRegisterView.as_view(),
        name="bankaccount_register",
    ),
    # BANK TRANSACTION MATCHING ACTIONS
    path(
        "transactions/<int:txn_id>/create-payment/",
        PaymentCreateFromTransactionView.as_view(),
        name="payment_create_from_transaction",
    ),

    path(
        "bank-transactions/<int:txn_id>/link-payment/",
        BankTransactionLinkPaymentView.as_view(),
        name="banktransaction_link_payment",
    ),

    path(
        "bank-transactions/<int:txn_id>/owner-equity/",
        BankTransactionMarkOwnerEquityView.as_view(),
        name="banktransaction_mark_owner_equity",
    ),

    path(
        "bank-transactions/<int:txn_id>/recategorize/",
        BankTransactionRecategorizeView.as_view(),
        name="banktransaction_recategorize",
    ),

    path(
        "bank-accounts/<int:pk>/recategorize/",
        BankTransactionBulkRecategorizeView.as_view(),
        name="banktransaction_bulk_recategorize",
    ),

    path(
        "bank-transactions/<int:txn_id>/unmatch/",
        BankTransactionUnmatchView.as_view(),
        name="banktransaction_unmatch",
    ),

    path(
        "bank-transaction/<int:pk>/link-expense/",
        banktransaction_link_expense,
        name="banktransaction_link_expense",
    ),

    path(
        "bank-transaction/<int:pk>/match-transfer/",
        banktransaction_match_transfer,
        name="banktransaction_match_transfer",
    ),

    # CSV IMPORT SUPPORT
    path(
        "bank-accounts/<int:pk>/import/",
        BankTransactionCSVImportView.as_view(),
        name="banktransaction_import",
    ),

    path("transactions/", BankTransactionListView.as_view(), name="banktransaction_list"),
    path("bank-transactions/filter-offset/", OffsetAccountFilterView.as_view(), name="filter_offset_account"),

    # BATCH MATCHING
    path(
        "bank-accounts/<int:pk>/match-expenses/",
        BatchMatchExpensesView.as_view(),
        name="batch_match_expenses",
    ),
    path(
        "bank-accounts/<int:pk>/match-payments/",
        BatchMatchPaymentsView.as_view(),
        name="batch_match_payments",
    ),

    # TRANSACTION RULES
    path("transaction-rules/", TransactionRuleListView.as_view(), name="transactionrule_list"),
    path("transaction-rules/new/", TransactionRuleCreateView.as_view(), name="transactionrule_create"),
    path("transaction-rules/<int:pk>/edit/", TransactionRuleUpdateView.as_view(), name="transactionrule_edit"),
    path("transaction-rules/<int:pk>/delete/", TransactionRuleDeleteView.as_view(), name="transactionrule_delete"),
]

