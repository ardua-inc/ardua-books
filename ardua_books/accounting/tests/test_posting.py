"""
Tests for GL posting and reversing logic.
"""
import pytest
from decimal import Decimal
from datetime import date, timedelta

from django.contrib.contenttypes.models import ContentType

from billing.models import Invoice, InvoiceStatus
from accounting.models import (
    ChartOfAccount,
    JournalEntry,
    JournalLine,
    AccountType,
)
from accounting.services.posting import (
    post_invoice,
    reverse_invoice,
    _invoice_currently_posted,
)
from conftest import ClientFactory, UserFactory


@pytest.fixture
def gl_accounts(db):
    """Create required GL accounts for posting tests."""
    ar, _ = ChartOfAccount.objects.get_or_create(
        code="1100",
        defaults={"name": "Accounts Receivable", "type": AccountType.ASSET},
    )
    revenue, _ = ChartOfAccount.objects.get_or_create(
        code="4000",
        defaults={"name": "Consulting Revenue", "type": AccountType.INCOME},
    )
    return {"ar": ar, "revenue": revenue}


@pytest.fixture
def issued_invoice(db, gl_accounts):
    """Create an issued invoice for testing."""
    client = ClientFactory()
    invoice = Invoice.objects.create(
        client=client,
        invoice_number="2025-POST01",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=30),
        status=InvoiceStatus.ISSUED,
        subtotal=Decimal("1500.00"),
        total=Decimal("1500.00"),
    )
    return invoice


# =============================================================================
# Invoice Posting Tests
# =============================================================================

class TestPostInvoice:
    def test_post_creates_journal_entry(self, db, gl_accounts, issued_invoice):
        """Test that posting creates a journal entry."""
        user = UserFactory()

        entry = post_invoice(issued_invoice, user=user)

        assert entry is not None
        assert entry.posted_by == user
        assert "posted" in entry.description.lower()

    def test_post_creates_correct_lines(self, db, gl_accounts, issued_invoice):
        """Test that posting creates correct debit/credit lines."""
        entry = post_invoice(issued_invoice)

        lines = list(entry.lines.all())
        assert len(lines) == 2

        # Find AR and Revenue lines
        ar_line = next(l for l in lines if l.account.code == "1100")
        rev_line = next(l for l in lines if l.account.code == "4000")

        # AR should be debited
        assert ar_line.debit == Decimal("1500.00")
        assert ar_line.credit == Decimal("0")

        # Revenue should be credited
        assert rev_line.debit == Decimal("0")
        assert rev_line.credit == Decimal("1500.00")

    def test_post_is_balanced(self, db, gl_accounts, issued_invoice):
        """Test that the journal entry is balanced (debits = credits)."""
        entry = post_invoice(issued_invoice)

        total_debits = sum(line.debit for line in entry.lines.all())
        total_credits = sum(line.credit for line in entry.lines.all())

        assert total_debits == total_credits

    def test_post_links_to_invoice(self, db, gl_accounts, issued_invoice):
        """Test that journal entry links back to invoice via generic FK."""
        entry = post_invoice(issued_invoice)

        ct = ContentType.objects.get_for_model(Invoice)
        assert entry.source_content_type == ct
        assert entry.source_object_id == issued_invoice.id

    def test_post_idempotent(self, db, gl_accounts, issued_invoice):
        """Test that posting twice doesn't create duplicate entries."""
        entry1 = post_invoice(issued_invoice)
        entry2 = post_invoice(issued_invoice)

        # Second call should return nothing (already posted)
        assert entry1 is not None
        assert entry2 is None

        # Should only have one entry
        ct = ContentType.objects.get_for_model(Invoice)
        count = JournalEntry.objects.filter(
            source_content_type=ct,
            source_object_id=issued_invoice.id,
        ).count()
        assert count == 1

    def test_invoice_currently_posted_flag(self, db, gl_accounts, issued_invoice):
        """Test the _invoice_currently_posted helper."""
        # Before posting
        assert _invoice_currently_posted(issued_invoice) is False

        # After posting
        post_invoice(issued_invoice)
        assert _invoice_currently_posted(issued_invoice) is True


# =============================================================================
# Invoice Reversal Tests
# =============================================================================

class TestReverseInvoice:
    def test_reverse_creates_reversing_entry(self, db, gl_accounts, issued_invoice):
        """Test that reversing creates a reversing journal entry."""
        user = UserFactory()

        # First post
        post_invoice(issued_invoice, user=user)

        # Then reverse
        reversal = reverse_invoice(issued_invoice, user=user)

        assert reversal is not None
        assert "reversed" in reversal.description.lower()

    def test_reverse_creates_opposite_lines(self, db, gl_accounts, issued_invoice):
        """Test that reversal has opposite debits/credits."""
        post_invoice(issued_invoice)
        reversal = reverse_invoice(issued_invoice)

        lines = list(reversal.lines.all())
        assert len(lines) == 2

        # Find lines
        ar_line = next(l for l in lines if l.account.code == "1100")
        rev_line = next(l for l in lines if l.account.code == "4000")

        # AR should be credited (opposite of posting)
        assert ar_line.debit == Decimal("0")
        assert ar_line.credit == Decimal("1500.00")

        # Revenue should be debited (opposite of posting)
        assert rev_line.debit == Decimal("1500.00")
        assert rev_line.credit == Decimal("0")

    def test_reverse_is_balanced(self, db, gl_accounts, issued_invoice):
        """Test that reversal entry is balanced."""
        post_invoice(issued_invoice)
        reversal = reverse_invoice(issued_invoice)

        total_debits = sum(line.debit for line in reversal.lines.all())
        total_credits = sum(line.credit for line in reversal.lines.all())

        assert total_debits == total_credits

    def test_reverse_without_post_does_nothing(self, db, gl_accounts, issued_invoice):
        """Test that reversing an unposted invoice does nothing."""
        # Try to reverse without posting first
        reversal = reverse_invoice(issued_invoice)
        assert reversal is None

    def test_reverse_updates_posted_flag(self, db, gl_accounts, issued_invoice):
        """Test that reversal updates the posted status."""
        post_invoice(issued_invoice)
        assert _invoice_currently_posted(issued_invoice) is True

        reverse_invoice(issued_invoice)
        assert _invoice_currently_posted(issued_invoice) is False

    def test_post_reverse_post_cycle(self, db, gl_accounts, issued_invoice):
        """Test full cycle: post -> reverse -> post again."""
        # Post
        entry1 = post_invoice(issued_invoice)
        assert entry1 is not None
        assert _invoice_currently_posted(issued_invoice) is True

        # Reverse
        reversal = reverse_invoice(issued_invoice)
        assert reversal is not None
        assert _invoice_currently_posted(issued_invoice) is False

        # Post again
        entry2 = post_invoice(issued_invoice)
        assert entry2 is not None
        assert _invoice_currently_posted(issued_invoice) is True

        # Should have 3 entries total
        ct = ContentType.objects.get_for_model(Invoice)
        count = JournalEntry.objects.filter(
            source_content_type=ct,
            source_object_id=issued_invoice.id,
        ).count()
        assert count == 3


# =============================================================================
# GL Balance Tests
# =============================================================================

class TestGLBalances:
    def test_ar_balance_after_posting(self, db, gl_accounts, issued_invoice):
        """Test that AR balance increases after posting."""
        ar_account = gl_accounts["ar"]

        # Get initial balance
        initial_balance = self._get_account_balance(ar_account)

        post_invoice(issued_invoice)

        # Balance should increase by invoice total
        new_balance = self._get_account_balance(ar_account)
        assert new_balance == initial_balance + Decimal("1500.00")

    def test_revenue_balance_after_posting(self, db, gl_accounts, issued_invoice):
        """Test that revenue balance increases after posting."""
        revenue_account = gl_accounts["revenue"]

        initial_balance = self._get_account_balance(revenue_account)

        post_invoice(issued_invoice)

        # Revenue is a credit-normal account, so credits increase it
        new_balance = self._get_account_balance(revenue_account)
        # For income accounts: credits - debits
        expected = initial_balance + Decimal("1500.00")
        assert new_balance == expected

    def test_balances_net_zero_after_reverse(self, db, gl_accounts, issued_invoice):
        """Test that balances net to zero after post + reverse."""
        ar_account = gl_accounts["ar"]

        initial_ar = self._get_account_balance(ar_account)

        post_invoice(issued_invoice)
        reverse_invoice(issued_invoice)

        final_ar = self._get_account_balance(ar_account)
        assert final_ar == initial_ar

    def _get_account_balance(self, account):
        """Helper to calculate account balance."""
        from django.db.models import Sum

        totals = JournalLine.objects.filter(account=account).aggregate(
            total_debit=Sum("debit"),
            total_credit=Sum("credit"),
        )

        debits = totals["total_debit"] or Decimal("0")
        credits = totals["total_credit"] or Decimal("0")

        # For asset accounts (like AR): balance = debits - credits
        # For income accounts: balance = credits - debits
        if account.type in [AccountType.ASSET, AccountType.EXPENSE]:
            return debits - credits
        else:
            return credits - debits
