"""
Chart-of-accounts codes that posting code depends on.

Each code is named once, here. Before 3.3.4 invoices debited AR 1100 while
payments credited a second account, 1200, also named "Accounts Receivable",
so AR was split across two accounts that never netted.
"""

CASH = "1000"  # payments not (yet) linked to a bank deposit: undeposited funds
ACCOUNTS_RECEIVABLE = "1100"
UNAPPLIED_PAYMENTS = "2200"
OWNER_EQUITY = "3000"
REVENUE = "4000"

# Added in 3.3.6 by migrations accounting 0016 / billing 0007 (with matching
# expense categories). Money the owner puts in, and business purchases the owner
# pays for personally, are a loan from the shareholder, not Owner Equity.
SHAREHOLDER_LOAN = "2500"
RENT = "6110"
VEHICLE = "6120"
REPAIRS = "6130"

# (code, name, type, expense category name or None) for the 3.3.6 migrations.
OWNER_FUNDING_ACCOUNTS = [
    (SHAREHOLDER_LOAN, "Loan from Shareholder", "LIABILITY", None),
    (RENT, "Rent", "EXPENSE", "Rent"),
    (VEHICLE, "Vehicle Expenses", "EXPENSE", "Vehicle Expenses"),
    (REPAIRS, "Repairs & Maintenance", "EXPENSE", "Repairs & Maintenance"),
]


def gl_account(code):
    from accounting.models import ChartOfAccount

    return ChartOfAccount.objects.get(code=code)
