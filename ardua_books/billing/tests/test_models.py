"""
Tests for billing models: Client, TimeEntry, Expense, Invoice, InvoiceLine.
"""
import pytest
from decimal import Decimal
from datetime import date, timedelta

from django.core.exceptions import ValidationError

from billing.models import (
    Client,
    TimeEntry,
    Expense,
    Invoice,
    InvoiceLine,
    BillableStatus,
    InvoiceStatus,
)
from conftest import (
    ClientFactory,
    ConsultantFactory,
    TimeEntryFactory,
    ExpenseFactory,
    ExpenseCategoryFactory,
    InvoiceFactory,
    InvoiceLineFactory,
)


# =============================================================================
# Client Model Tests
# =============================================================================

class TestClient:
    def test_client_creation(self, db):
        """Test basic client creation with required fields."""
        client = ClientFactory(
            name="Acme Corp",
            default_hourly_rate=Decimal("200.00"),
            payment_terms_days=45,
        )

        assert client.name == "Acme Corp"
        assert client.default_hourly_rate == Decimal("200.00")
        assert client.payment_terms_days == 45
        assert client.is_active is True

    def test_client_name_unique(self, db):
        """Test that client names must be unique."""
        ClientFactory(name="Unique Client")

        with pytest.raises(Exception):  # IntegrityError
            ClientFactory(name="Unique Client")

    def test_client_str(self, db):
        """Test client string representation."""
        client = ClientFactory(name="Test Client")
        assert str(client) == "Test Client"


# =============================================================================
# TimeEntry Model Tests
# =============================================================================

class TestTimeEntry:
    def test_time_entry_creation(self, db):
        """Test basic time entry creation."""
        entry = TimeEntryFactory(
            hours=Decimal("4.50"),
            billing_rate=Decimal("150.00"),
            description="Development work",
        )

        assert entry.hours == Decimal("4.50")
        assert entry.billing_rate == Decimal("150.00")
        assert entry.status == BillableStatus.UNBILLED

    def test_time_entry_default_status(self, db):
        """Test that new time entries default to UNBILLED."""
        entry = TimeEntryFactory()
        assert entry.status == BillableStatus.UNBILLED

    def test_time_entry_ordering(self, db):
        """Test that time entries are ordered by date descending."""
        client = ClientFactory()
        consultant = ConsultantFactory()

        entry1 = TimeEntryFactory(
            client=client,
            consultant=consultant,
            work_date=date.today() - timedelta(days=2),
        )
        entry2 = TimeEntryFactory(
            client=client,
            consultant=consultant,
            work_date=date.today(),
        )
        entry3 = TimeEntryFactory(
            client=client,
            consultant=consultant,
            work_date=date.today() - timedelta(days=1),
        )

        entries = list(TimeEntry.objects.filter(client=client))
        assert entries[0] == entry2  # Most recent first
        assert entries[1] == entry3
        assert entries[2] == entry1


# =============================================================================
# Expense Model Tests
# =============================================================================

class TestExpense:
    def test_expense_creation(self, db):
        """Test basic expense creation."""
        expense = ExpenseFactory(
            amount=Decimal("250.00"),
            description="Flight to client site",
            billable=True,
        )

        assert expense.amount == Decimal("250.00")
        assert expense.billable is True
        assert expense.status == BillableStatus.UNBILLED

    def test_expense_without_client_if_not_billable(self, db):
        """Test that non-billable expenses can exist without a client."""
        category = ExpenseCategoryFactory()
        expense = Expense.objects.create(
            client=None,
            category=category,
            expense_date=date.today(),
            amount=Decimal("50.00"),
            description="Office supplies",
            billable=False,
        )

        assert expense.client is None
        assert expense.billable is False


# =============================================================================
# Invoice Model Tests
# =============================================================================

class TestInvoice:
    def test_invoice_auto_number_generation(self, db):
        """Test that invoice numbers are auto-generated."""
        client = ClientFactory()
        invoice = Invoice.objects.create(
            client=client,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
        )

        assert invoice.invoice_number is not None
        assert len(invoice.invoice_number) > 0
        assert invoice.sequence > 0

    def test_invoice_sequence_increments(self, db):
        """Test that invoice sequence numbers increment."""
        client = ClientFactory()

        invoice1 = Invoice.objects.create(
            client=client,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            status=InvoiceStatus.ISSUED,  # Avoid draft conflict
        )

        invoice2 = Invoice.objects.create(
            client=client,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
        )

        assert invoice2.sequence == invoice1.sequence + 1

    def test_one_draft_per_client(self, db):
        """Test that only one draft invoice per client is allowed."""
        client = ClientFactory()

        # First draft should succeed
        Invoice.objects.create(
            client=client,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            status=InvoiceStatus.DRAFT,
        )

        # Second draft should fail
        with pytest.raises(ValidationError) as exc_info:
            Invoice.objects.create(
                client=client,
                issue_date=date.today(),
                due_date=date.today() + timedelta(days=30),
                status=InvoiceStatus.DRAFT,
            )

        assert "already has an invoice in Draft status" in str(exc_info.value)

    def test_multiple_drafts_different_clients(self, db):
        """Test that different clients can have draft invoices."""
        client1 = ClientFactory()
        client2 = ClientFactory()

        invoice1 = Invoice.objects.create(
            client=client1,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            status=InvoiceStatus.DRAFT,
        )

        invoice2 = Invoice.objects.create(
            client=client2,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            status=InvoiceStatus.DRAFT,
        )

        assert invoice1.status == InvoiceStatus.DRAFT
        assert invoice2.status == InvoiceStatus.DRAFT

    def test_invoice_recalculate_totals(self, db):
        """Test that invoice totals are correctly calculated from lines."""
        client = ClientFactory()
        invoice = Invoice.objects.create(
            client=client,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
        )

        # Add lines
        InvoiceLine.objects.create(
            invoice=invoice,
            line_type=InvoiceLine.LineType.TIME,
            description="8 hours development",
            quantity=Decimal("8.00"),
            unit_price=Decimal("150.00"),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            line_type=InvoiceLine.LineType.EXPENSE,
            description="Travel expense",
            quantity=Decimal("1.00"),
            unit_price=Decimal("250.00"),
        )

        invoice.recalculate_totals()

        # 8 * 150 = 1200, plus 250 = 1450
        assert invoice.subtotal == Decimal("1450.00")
        assert invoice.total == Decimal("1450.00")
        assert invoice.tax_amount == Decimal("0.00")

    def test_invoice_outstanding_balance(self, db, default_accounts):
        """Test outstanding balance calculation."""
        from accounting.models import Payment, PaymentApplication

        client = ClientFactory()
        invoice = Invoice.objects.create(
            client=client,
            invoice_number="2025-TEST",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            total=Decimal("1000.00"),
            status=InvoiceStatus.ISSUED,
        )

        # No payments - balance should equal total
        assert invoice.outstanding_balance() == Decimal("1000.00")

        # Add a partial payment
        payment = Payment.objects.create(
            client=client,
            date=date.today(),
            amount=Decimal("400.00"),
            method="check",
        )
        PaymentApplication.objects.create(
            payment=payment,
            invoice=invoice,
            amount=Decimal("400.00"),
        )

        assert invoice.outstanding_balance() == Decimal("600.00")
        assert invoice.is_paid() is False

    def test_invoice_is_paid(self, db, default_accounts):
        """Test is_paid() when fully paid."""
        from accounting.models import Payment, PaymentApplication

        client = ClientFactory()
        invoice = Invoice.objects.create(
            client=client,
            invoice_number="2025-PAID",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            total=Decimal("500.00"),
            status=InvoiceStatus.ISSUED,
        )

        payment = Payment.objects.create(
            client=client,
            date=date.today(),
            amount=Decimal("500.00"),
            method="check",
        )
        PaymentApplication.objects.create(
            payment=payment,
            invoice=invoice,
            amount=Decimal("500.00"),
        )

        assert invoice.outstanding_balance() == Decimal("0.00")
        assert invoice.is_paid() is True


# =============================================================================
# InvoiceLine Model Tests
# =============================================================================

class TestInvoiceLine:
    def test_line_total_auto_calculation(self, db):
        """Test that line_total is automatically calculated on save."""
        client = ClientFactory()
        invoice = Invoice.objects.create(
            client=client,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
        )

        line = InvoiceLine.objects.create(
            invoice=invoice,
            line_type=InvoiceLine.LineType.TIME,
            description="Development work",
            quantity=Decimal("10.00"),
            unit_price=Decimal("175.00"),
        )

        assert line.line_total == Decimal("1750.00")

    def test_line_total_updates_on_change(self, db):
        """Test that line_total updates when quantity/price change."""
        client = ClientFactory()
        invoice = Invoice.objects.create(
            client=client,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
        )

        line = InvoiceLine.objects.create(
            invoice=invoice,
            line_type=InvoiceLine.LineType.GENERAL,
            description="Service",
            quantity=Decimal("1.00"),
            unit_price=Decimal("100.00"),
        )

        assert line.line_total == Decimal("100.00")

        # Update quantity
        line.quantity = Decimal("5.00")
        line.save()

        assert line.line_total == Decimal("500.00")

    def test_line_types(self, db):
        """Test different line types can be created."""
        client = ClientFactory()
        invoice = Invoice.objects.create(
            client=client,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
        )

        for line_type in [
            InvoiceLine.LineType.TIME,
            InvoiceLine.LineType.EXPENSE,
            InvoiceLine.LineType.ADJUSTMENT,
            InvoiceLine.LineType.GENERAL,
        ]:
            line = InvoiceLine.objects.create(
                invoice=invoice,
                line_type=line_type,
                description=f"Test {line_type}",
                quantity=Decimal("1.00"),
                unit_price=Decimal("100.00"),
            )
            assert line.line_type == line_type
