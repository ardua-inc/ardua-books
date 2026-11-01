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


def gl_account(code):
    from accounting.models import ChartOfAccount

    return ChartOfAccount.objects.get(code=code)
