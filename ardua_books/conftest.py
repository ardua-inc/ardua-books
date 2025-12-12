"""
Shared pytest fixtures and factories for Ardua Books tests.
"""
import pytest
from decimal import Decimal
from datetime import date, timedelta

import factory
from factory.django import DjangoModelFactory
from django.contrib.auth import get_user_model

from billing.models import (
    Client,
    Consultant,
    ExpenseCategory,
    TimeEntry,
    Expense,
    Invoice,
    InvoiceLine,
    BillableStatus,
    InvoiceStatus,
)
from accounting.models import (
    ChartOfAccount,
    AccountType,
    JournalEntry,
    JournalLine,
    Payment,
    PaymentApplication,
    PaymentMethod,
)

User = get_user_model()


# =============================================================================
# User Factory
# =============================================================================

class UserFactory(DjangoModelFactory):
    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@example.com")
    password = factory.PostGenerationMethodCall("set_password", "testpass123")


# =============================================================================
# Billing Factories
# =============================================================================

class ClientFactory(DjangoModelFactory):
    class Meta:
        model = Client

    name = factory.Sequence(lambda n: f"Client {n}")
    billing_address = "123 Test Street\nTest City, TS 12345"
    email = factory.LazyAttribute(lambda obj: f"{obj.name.lower().replace(' ', '')}@example.com")
    phone = "555-1234"
    default_hourly_rate = Decimal("150.00")
    payment_terms_days = 30
    is_active = True


class ConsultantFactory(DjangoModelFactory):
    class Meta:
        model = Consultant

    user = factory.SubFactory(UserFactory)
    display_name = factory.LazyAttribute(lambda obj: f"Consultant {obj.user.username}")
    default_hourly_rate = Decimal("175.00")


class ExpenseCategoryFactory(DjangoModelFactory):
    class Meta:
        model = ExpenseCategory

    name = factory.Sequence(lambda n: f"Category {n}")
    billable_by_default = True


class TimeEntryFactory(DjangoModelFactory):
    class Meta:
        model = TimeEntry

    client = factory.SubFactory(ClientFactory)
    consultant = factory.SubFactory(ConsultantFactory)
    work_date = factory.LazyFunction(date.today)
    hours = Decimal("8.00")
    description = factory.Sequence(lambda n: f"Work performed - task {n}")
    billing_rate = Decimal("150.00")
    status = BillableStatus.UNBILLED


class ExpenseFactory(DjangoModelFactory):
    class Meta:
        model = Expense

    client = factory.SubFactory(ClientFactory)
    category = factory.SubFactory(ExpenseCategoryFactory)
    expense_date = factory.LazyFunction(date.today)
    amount = Decimal("100.00")
    description = factory.Sequence(lambda n: f"Expense {n}")
    billable = True
    status = BillableStatus.UNBILLED


class InvoiceFactory(DjangoModelFactory):
    class Meta:
        model = Invoice

    client = factory.SubFactory(ClientFactory)
    invoice_number = factory.Sequence(lambda n: f"2025-{n:03d}")
    issue_date = factory.LazyFunction(date.today)
    due_date = factory.LazyAttribute(
        lambda obj: obj.issue_date + timedelta(days=obj.client.payment_terms_days)
    )
    status = InvoiceStatus.DRAFT
    notes = ""
    subtotal = Decimal("0.00")
    tax_amount = Decimal("0.00")
    total = Decimal("0.00")


class InvoiceLineFactory(DjangoModelFactory):
    class Meta:
        model = InvoiceLine

    invoice = factory.SubFactory(InvoiceFactory)
    line_type = InvoiceLine.LineType.GENERAL
    description = factory.Sequence(lambda n: f"Line item {n}")
    quantity = Decimal("1.00")
    unit_price = Decimal("100.00")


# =============================================================================
# Accounting Factories
# =============================================================================

class ChartOfAccountFactory(DjangoModelFactory):
    class Meta:
        model = ChartOfAccount

    code = factory.Sequence(lambda n: f"{5000 + n}")
    name = factory.Sequence(lambda n: f"Account {n}")
    type = AccountType.EXPENSE
    is_active = True


class JournalEntryFactory(DjangoModelFactory):
    class Meta:
        model = JournalEntry

    posted_by = factory.SubFactory(UserFactory)
    description = factory.Sequence(lambda n: f"Journal Entry {n}")


class PaymentFactory(DjangoModelFactory):
    class Meta:
        model = Payment

    client = factory.SubFactory(ClientFactory)
    date = factory.LazyFunction(date.today)
    amount = Decimal("1000.00")
    method = PaymentMethod.CHECK
    memo = factory.Sequence(lambda n: f"Payment {n}")
    unapplied_amount = Decimal("0.00")


# =============================================================================
# Pytest Fixtures
# =============================================================================

@pytest.fixture
def user(db):
    """Create a test user."""
    return UserFactory()


@pytest.fixture
def client_obj(db):
    """Create a test client (named client_obj to avoid pytest's client fixture)."""
    return ClientFactory()


@pytest.fixture
def consultant(db, user):
    """Create a test consultant linked to user."""
    return ConsultantFactory(user=user)


@pytest.fixture
def expense_category(db):
    """Create a test expense category."""
    return ExpenseCategoryFactory(name="Travel")


@pytest.fixture
def default_accounts(db):
    """Ensure default Chart of Accounts exist (normally created by migration)."""
    accounts = {}

    defaults = [
        ("1000", "Cash", AccountType.ASSET),
        ("1100", "Accounts Receivable", AccountType.ASSET),
        ("1200", "Accounts Receivable - Applied", AccountType.ASSET),
        ("2200", "Unapplied Payments", AccountType.LIABILITY),
        ("3000", "Owner Equity", AccountType.EQUITY),
        ("4000", "Consulting Revenue", AccountType.INCOME),
    ]

    for code, name, acct_type in defaults:
        acct, _ = ChartOfAccount.objects.get_or_create(
            code=code,
            defaults={"name": name, "type": acct_type}
        )
        accounts[code] = acct

    return accounts
