"""
Fixtures shared by the ledger tests (test_ledger_postings, test_owner_funding).
"""

import pytest

from accounting import gl_codes
from accounting.models import AccountType, BankAccountType, ChartOfAccount
from accounting.tests.helpers import make_account
from conftest import ChartOfAccountFactory, ExpenseCategoryFactory, UserFactory


@pytest.fixture
def gl(db):
    """The fixed accounts posting code looks up by code."""
    accounts = {}
    for code, name, kind in [
        (gl_codes.CASH, "Cash", AccountType.ASSET),
        (gl_codes.ACCOUNTS_RECEIVABLE, "Accounts Receivable", AccountType.ASSET),
        ("1200", "Accounts Receivable", AccountType.ASSET),  # the legacy second AR
        (gl_codes.UNAPPLIED_PAYMENTS, "Unapplied Payments", AccountType.LIABILITY),
        (gl_codes.OWNER_EQUITY, "Owner Equity", AccountType.EQUITY),
        (gl_codes.REVENUE, "Consulting Revenue", AccountType.INCOME),
    ]:
        accounts[code], _ = ChartOfAccount.objects.get_or_create(
            code=code, defaults={"name": name, "type": kind}
        )
    return accounts


@pytest.fixture
def checking(gl):
    return make_account(BankAccountType.CHECKING)


@pytest.fixture
def card(gl):
    # Chase-style: charges negative, payments and credits positive.
    return make_account(BankAccountType.CREDIT_CARD)


@pytest.fixture
def other_card(gl):
    return make_account(BankAccountType.CREDIT_CARD)


@pytest.fixture
def travel(db):
    """An expense category with its GL account."""
    return ExpenseCategoryFactory(name="Travel", account=ChartOfAccountFactory(type=AccountType.EXPENSE))


@pytest.fixture
def logged_in(client, db):
    client.force_login(UserFactory())
    return client
