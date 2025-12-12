from django.urls import path

from .views import (
    BankAccountListView,
    BankAccountCreateView,
    BankAccountDetailView,
    BankRegisterView,
    BankTransactionListView,
    BankTransactionListForAccountView,
    BankTransactionCreateView,
    BankTransactionDetailView,
    BankTransactionLinkPaymentView,
    BankTransactionMarkOwnerEquityView,
    BankTransactionCSVImportView,
    JournalEntryListView, 
    JournalEntryDetailView,
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
)

app_name = "accounting"

urlpatterns = [
    path("", ReportsHomeView.as_view(), name="home"),
    path("trial-balance/", TrialBalanceView.as_view(), name="trial_balance"),
    path("income-statement/", IncomeStatementView.as_view(), name="income_statement"),
    path("journal/", JournalEntryListView.as_view(), name="journal_list"),
    path("journal/<int:pk>/", JournalEntryDetailView.as_view(), name="journal_detail"),
    path("payments/<int:pk>/", PaymentDetailView.as_view(), name="payment_detail"),
    path("payments/", PaymentListView.as_view(), name="payment_list"),
    path("payments/create/", PaymentCreateGeneralView.as_view(), name="payment_create_general"),
    path("payment/invoice-fragment/", payment_invoice_fragment, name="payment_invoice_fragment"),
    path("reports/ar-aging/", ARAgingView.as_view(), name="ar_aging"),
    path("reports/client-balances/", ClientBalanceSummaryView.as_view(), name="client_balance_summary"),
    # BANK ACCOUNTS
    path("bank-accounts/", BankAccountListView.as_view(), name="bankaccount_list"),
    path("bank-accounts/new/", BankAccountCreateView.as_view(), name="bankaccount_create"),
    path("bank-accounts/<int:pk>/", BankAccountDetailView.as_view(), name="bankaccount_detail"),

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
    # CSV IMPORT SUPPORT
    path(
        "bank-accounts/<int:pk>/import/",
        BankTransactionCSVImportView.as_view(),
        name="banktransaction_import",
    ),

    path("transactions/", BankTransactionListView.as_view(), name="banktransaction_list"),
    path("bank-transactions/filter-offset/", OffsetAccountFilterView.as_view(), name="filter_offset_account"),

]

