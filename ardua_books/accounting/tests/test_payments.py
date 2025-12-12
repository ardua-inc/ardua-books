"""
Tests for payment processing and allocation.
"""
import pytest
from decimal import Decimal
from datetime import date, timedelta

from django.contrib.contenttypes.models import ContentType

from billing.models import Invoice, InvoiceStatus
from accounting.models import (
    ChartOfAccount,
    AccountType,
    Payment,
    PaymentApplication,
    PaymentMethod,
    JournalEntry,
    JournalLine,
)
from conftest import ClientFactory, UserFactory


@pytest.fixture
def payment_accounts(db):
    """Create required GL accounts for payment tests."""
    accounts = {}

    account_defs = [
        ("1000", "Cash", AccountType.ASSET),
        ("1100", "Accounts Receivable", AccountType.ASSET),
        ("1200", "Accounts Receivable - Applied", AccountType.ASSET),
        ("2200", "Unapplied Payments", AccountType.LIABILITY),
        ("4000", "Consulting Revenue", AccountType.INCOME),
    ]

    for code, name, acct_type in account_defs:
        acct, _ = ChartOfAccount.objects.get_or_create(
            code=code,
            defaults={"name": name, "type": acct_type},
        )
        accounts[code] = acct

    return accounts


@pytest.fixture
def client_with_invoice(db, payment_accounts):
    """Create a client with an issued invoice."""
    client = ClientFactory()
    invoice = Invoice.objects.create(
        client=client,
        invoice_number="2025-PAY01",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=30),
        status=InvoiceStatus.ISSUED,
        subtotal=Decimal("1000.00"),
        total=Decimal("1000.00"),
    )
    return client, invoice


# =============================================================================
# Payment Model Tests
# =============================================================================

class TestPaymentModel:
    def test_payment_creation(self, db, payment_accounts):
        """Test basic payment creation."""
        client = ClientFactory()

        payment = Payment.objects.create(
            client=client,
            date=date.today(),
            amount=Decimal("500.00"),
            method=PaymentMethod.CHECK,
            memo="Check #1234",
        )

        assert payment.amount == Decimal("500.00")
        assert payment.method == PaymentMethod.CHECK
        assert payment.unapplied_amount == Decimal("0")

    def test_payment_methods(self, db, payment_accounts):
        """Test all payment methods can be used."""
        client = ClientFactory()

        for method in [
            PaymentMethod.CHECK,
            PaymentMethod.ACH,
            PaymentMethod.CASH,
            PaymentMethod.CARD,
            PaymentMethod.OTHER,
        ]:
            payment = Payment.objects.create(
                client=client,
                date=date.today(),
                amount=Decimal("100.00"),
                method=method,
            )
            assert payment.method == method


# =============================================================================
# Payment Application Tests
# =============================================================================

class TestPaymentApplication:
    def test_apply_full_payment(self, db, payment_accounts, client_with_invoice):
        """Test applying full payment to an invoice."""
        client, invoice = client_with_invoice

        payment = Payment.objects.create(
            client=client,
            date=date.today(),
            amount=Decimal("1000.00"),
            method=PaymentMethod.CHECK,
        )

        application = PaymentApplication.objects.create(
            payment=payment,
            invoice=invoice,
            amount=Decimal("1000.00"),
        )

        assert application.amount == Decimal("1000.00")
        assert invoice.outstanding_balance() == Decimal("0.00")
        assert invoice.is_paid() is True

    def test_apply_partial_payment(self, db, payment_accounts, client_with_invoice):
        """Test applying partial payment to an invoice."""
        client, invoice = client_with_invoice

        payment = Payment.objects.create(
            client=client,
            date=date.today(),
            amount=Decimal("400.00"),
            method=PaymentMethod.ACH,
        )

        PaymentApplication.objects.create(
            payment=payment,
            invoice=invoice,
            amount=Decimal("400.00"),
        )

        assert invoice.outstanding_balance() == Decimal("600.00")
        assert invoice.is_paid() is False

    def test_multiple_payments_to_invoice(self, db, payment_accounts, client_with_invoice):
        """Test multiple payments applied to single invoice."""
        client, invoice = client_with_invoice

        # First payment
        payment1 = Payment.objects.create(
            client=client,
            date=date.today(),
            amount=Decimal("300.00"),
            method=PaymentMethod.CHECK,
        )
        PaymentApplication.objects.create(
            payment=payment1,
            invoice=invoice,
            amount=Decimal("300.00"),
        )

        assert invoice.outstanding_balance() == Decimal("700.00")

        # Second payment
        payment2 = Payment.objects.create(
            client=client,
            date=date.today(),
            amount=Decimal("700.00"),
            method=PaymentMethod.ACH,
        )
        PaymentApplication.objects.create(
            payment=payment2,
            invoice=invoice,
            amount=Decimal("700.00"),
        )

        assert invoice.outstanding_balance() == Decimal("0.00")
        assert invoice.is_paid() is True

    def test_payment_to_multiple_invoices(self, db, payment_accounts):
        """Test single payment applied to multiple invoices."""
        client = ClientFactory()

        invoice1 = Invoice.objects.create(
            client=client,
            invoice_number="2025-MULTI01",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            status=InvoiceStatus.ISSUED,
            total=Decimal("500.00"),
        )
        invoice2 = Invoice.objects.create(
            client=client,
            invoice_number="2025-MULTI02",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            status=InvoiceStatus.ISSUED,
            total=Decimal("300.00"),
        )

        # One payment for both
        payment = Payment.objects.create(
            client=client,
            date=date.today(),
            amount=Decimal("800.00"),
            method=PaymentMethod.CHECK,
        )

        PaymentApplication.objects.create(
            payment=payment,
            invoice=invoice1,
            amount=Decimal("500.00"),
        )
        PaymentApplication.objects.create(
            payment=payment,
            invoice=invoice2,
            amount=Decimal("300.00"),
        )

        assert invoice1.is_paid() is True
        assert invoice2.is_paid() is True

        # Verify total applications match payment
        total_applied = sum(app.amount for app in payment.applications.all())
        assert total_applied == payment.amount


# =============================================================================
# Payment GL Posting Tests
# =============================================================================

class TestPaymentGLPosting:
    def test_post_payment_creates_journal_entry(
        self, db, payment_accounts, client_with_invoice
    ):
        """Test that posting payment creates a journal entry."""
        client, invoice = client_with_invoice
        user = UserFactory()

        payment = Payment.objects.create(
            client=client,
            date=date.today(),
            amount=Decimal("1000.00"),
            method=PaymentMethod.CHECK,
        )
        PaymentApplication.objects.create(
            payment=payment,
            invoice=invoice,
            amount=Decimal("1000.00"),
        )

        entry = payment.post_to_accounting(user=user)

        assert entry is not None
        assert entry.posted_by == user

    def test_post_payment_correct_lines_full_application(
        self, db, payment_accounts, client_with_invoice
    ):
        """Test GL lines for fully applied payment."""
        client, invoice = client_with_invoice

        payment = Payment.objects.create(
            client=client,
            date=date.today(),
            amount=Decimal("1000.00"),
            method=PaymentMethod.CHECK,
        )
        PaymentApplication.objects.create(
            payment=payment,
            invoice=invoice,
            amount=Decimal("1000.00"),
        )

        entry = payment.post_to_accounting()
        lines = list(entry.lines.all())

        # Should have 2 lines: DR Cash, CR AR
        assert len(lines) == 2

        cash_line = next(l for l in lines if l.account.code == "1000")
        ar_line = next(l for l in lines if l.account.code == "1200")

        assert cash_line.debit == Decimal("1000.00")
        assert cash_line.credit == Decimal("0")
        assert ar_line.debit == Decimal("0")
        assert ar_line.credit == Decimal("1000.00")

    def test_post_payment_with_unapplied_amount(self, db, payment_accounts):
        """Test GL lines when payment has unapplied amount."""
        client = ClientFactory()

        invoice = Invoice.objects.create(
            client=client,
            invoice_number="2025-UNAPP01",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            status=InvoiceStatus.ISSUED,
            total=Decimal("500.00"),
        )

        # Payment is more than invoice
        payment = Payment.objects.create(
            client=client,
            date=date.today(),
            amount=Decimal("750.00"),
            method=PaymentMethod.CHECK,
            unapplied_amount=Decimal("250.00"),
        )
        PaymentApplication.objects.create(
            payment=payment,
            invoice=invoice,
            amount=Decimal("500.00"),
        )

        entry = payment.post_to_accounting()
        lines = list(entry.lines.all())

        # Should have 3 lines: DR Cash, CR AR, CR Unapplied
        assert len(lines) == 3

        cash_line = next(l for l in lines if l.account.code == "1000")
        ar_line = next(l for l in lines if l.account.code == "1200")
        unapplied_line = next(l for l in lines if l.account.code == "2200")

        assert cash_line.debit == Decimal("750.00")
        assert ar_line.credit == Decimal("500.00")
        assert unapplied_line.credit == Decimal("250.00")

    def test_post_payment_is_balanced(self, db, payment_accounts, client_with_invoice):
        """Test that payment GL entry is balanced."""
        client, invoice = client_with_invoice

        payment = Payment.objects.create(
            client=client,
            date=date.today(),
            amount=Decimal("1000.00"),
            method=PaymentMethod.CHECK,
        )
        PaymentApplication.objects.create(
            payment=payment,
            invoice=invoice,
            amount=Decimal("1000.00"),
        )

        entry = payment.post_to_accounting()

        total_debits = sum(line.debit for line in entry.lines.all())
        total_credits = sum(line.credit for line in entry.lines.all())

        assert total_debits == total_credits

    def test_post_payment_idempotent(self, db, payment_accounts, client_with_invoice):
        """Test that posting payment twice doesn't create duplicates."""
        client, invoice = client_with_invoice

        payment = Payment.objects.create(
            client=client,
            date=date.today(),
            amount=Decimal("1000.00"),
            method=PaymentMethod.CHECK,
        )
        PaymentApplication.objects.create(
            payment=payment,
            invoice=invoice,
            amount=Decimal("1000.00"),
        )

        entry1 = payment.post_to_accounting()
        entry2 = payment.post_to_accounting()

        # Second call should return existing entry
        assert entry1.id == entry2.id

        # Only one entry should exist
        ct = ContentType.objects.get_for_model(Payment)
        count = JournalEntry.objects.filter(
            source_content_type=ct,
            source_object_id=payment.id,
        ).count()
        assert count == 1

    def test_post_payment_links_to_payment(
        self, db, payment_accounts, client_with_invoice
    ):
        """Test that journal entry links to payment via generic FK."""
        client, invoice = client_with_invoice

        payment = Payment.objects.create(
            client=client,
            date=date.today(),
            amount=Decimal("1000.00"),
            method=PaymentMethod.CHECK,
        )
        PaymentApplication.objects.create(
            payment=payment,
            invoice=invoice,
            amount=Decimal("1000.00"),
        )

        entry = payment.post_to_accounting()

        ct = ContentType.objects.get_for_model(Payment)
        assert entry.source_content_type == ct
        assert entry.source_object_id == payment.id
