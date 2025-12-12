"""
Tests for billing services: invoice number generation, item attachment/detachment.
"""
import pytest
from decimal import Decimal
from datetime import date, timedelta
from unittest.mock import patch

from billing.models import (
    Invoice,
    InvoiceLine,
    TimeEntry,
    Expense,
    BillableStatus,
    InvoiceStatus,
)
from billing.services import (
    generate_next_invoice_number,
    attach_unbilled_items_to_invoice,
    detach_invoice_lines,
    mark_all_te_ex_unbilled_and_unlink,
    mark_te_ex_unbilled_keep_invoice_lines,
)
from conftest import (
    ClientFactory,
    ConsultantFactory,
    ExpenseCategoryFactory,
    TimeEntryFactory,
    ExpenseFactory,
    InvoiceFactory,
)


# =============================================================================
# Invoice Number Generation Tests
# =============================================================================

class TestGenerateNextInvoiceNumber:
    def test_first_invoice_of_year(self, db):
        """Test first invoice number is YYYY-001."""
        number = generate_next_invoice_number()
        year = date.today().year
        assert number == f"{year}-001"

    def test_increments_sequence(self, db):
        """Test that sequence increments correctly."""
        client = ClientFactory()
        year = date.today().year

        # Create first invoice
        Invoice.objects.create(
            client=client,
            invoice_number=f"{year}-001",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            status=InvoiceStatus.ISSUED,
        )

        number = generate_next_invoice_number()
        assert number == f"{year}-002"

    def test_handles_gaps_in_sequence(self, db):
        """Test that it finds the highest number even with gaps."""
        client = ClientFactory()
        year = date.today().year

        # Create invoices with a gap
        Invoice.objects.create(
            client=client,
            invoice_number=f"{year}-001",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            status=InvoiceStatus.ISSUED,
        )
        Invoice.objects.create(
            client=client,
            invoice_number=f"{year}-005",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            status=InvoiceStatus.ISSUED,
        )

        number = generate_next_invoice_number()
        assert number == f"{year}-006"

    def test_ignores_other_year_invoices(self, db):
        """Test that invoices from other years don't affect numbering."""
        client = ClientFactory()
        year = date.today().year

        # Create invoice from last year
        Invoice.objects.create(
            client=client,
            invoice_number=f"{year - 1}-099",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            status=InvoiceStatus.ISSUED,
        )

        number = generate_next_invoice_number()
        assert number == f"{year}-001"

    def test_handles_three_digit_sequence(self, db):
        """Test proper formatting for numbers > 99."""
        client = ClientFactory()
        year = date.today().year

        Invoice.objects.create(
            client=client,
            invoice_number=f"{year}-099",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            status=InvoiceStatus.ISSUED,
        )

        number = generate_next_invoice_number()
        assert number == f"{year}-100"


# =============================================================================
# Item Attachment Tests
# =============================================================================

class TestAttachUnbilledItems:
    def test_attach_time_entries(self, db):
        """Test attaching time entries to an invoice."""
        client = ClientFactory()
        consultant = ConsultantFactory()

        # Create unbilled time entries
        te1 = TimeEntryFactory(
            client=client,
            consultant=consultant,
            hours=Decimal("4.00"),
            billing_rate=Decimal("150.00"),
        )
        te2 = TimeEntryFactory(
            client=client,
            consultant=consultant,
            hours=Decimal("2.50"),
            billing_rate=Decimal("150.00"),
        )

        # Create invoice
        invoice = Invoice.objects.create(
            client=client,
            invoice_number="2025-TEST1",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
        )

        # Attach time entries
        attach_unbilled_items_to_invoice(invoice, [te1.id, te2.id], [])

        # Verify lines were created
        assert invoice.lines.count() == 2

        # Verify time entries are linked and marked BILLED
        te1.refresh_from_db()
        te2.refresh_from_db()
        assert te1.status == BillableStatus.BILLED
        assert te2.status == BillableStatus.BILLED
        assert te1.invoice_line is not None
        assert te2.invoice_line is not None

        # Verify line totals (check both values exist, order may vary)
        line_totals = sorted([line.line_total for line in invoice.lines.all()])
        assert line_totals == [Decimal("375.00"), Decimal("600.00")]  # 2.5*150, 4*150

    def test_attach_expenses(self, db):
        """Test attaching expenses to an invoice."""
        client = ClientFactory()
        category = ExpenseCategoryFactory()

        # Create unbilled expense
        expense = ExpenseFactory(
            client=client,
            category=category,
            amount=Decimal("250.00"),
        )

        # Create invoice
        invoice = Invoice.objects.create(
            client=client,
            invoice_number="2025-TEST2",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
        )

        # Attach expense
        attach_unbilled_items_to_invoice(invoice, [], [expense.id])

        # Verify
        assert invoice.lines.count() == 1
        expense.refresh_from_db()
        assert expense.status == BillableStatus.BILLED
        assert expense.invoice_line is not None

        line = invoice.lines.first()
        assert line.line_type == InvoiceLine.LineType.EXPENSE
        assert line.quantity == Decimal("1")
        assert line.unit_price == Decimal("250.00")
        assert line.line_total == Decimal("250.00")

    def test_attach_mixed_items(self, db):
        """Test attaching both time entries and expenses."""
        client = ClientFactory()
        consultant = ConsultantFactory()
        category = ExpenseCategoryFactory()

        te = TimeEntryFactory(
            client=client,
            consultant=consultant,
            hours=Decimal("8.00"),
            billing_rate=Decimal("175.00"),
        )
        expense = ExpenseFactory(
            client=client,
            category=category,
            amount=Decimal("100.00"),
        )

        invoice = Invoice.objects.create(
            client=client,
            invoice_number="2025-TEST3",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
        )

        attach_unbilled_items_to_invoice(invoice, [te.id], [expense.id])

        assert invoice.lines.count() == 2

        # Recalculate and verify total
        invoice.recalculate_totals()
        expected_total = Decimal("8.00") * Decimal("175.00") + Decimal("100.00")
        assert invoice.total == expected_total  # 1400 + 100 = 1500


# =============================================================================
# Item Detachment Tests
# =============================================================================

class TestDetachInvoiceLines:
    def test_detach_time_entry_line(self, db):
        """Test detaching a time entry line from invoice."""
        client = ClientFactory()
        consultant = ConsultantFactory()

        te = TimeEntryFactory(
            client=client,
            consultant=consultant,
            hours=Decimal("4.00"),
            billing_rate=Decimal("150.00"),
        )

        invoice = Invoice.objects.create(
            client=client,
            invoice_number="2025-DETACH1",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
        )

        # Attach and then detach
        attach_unbilled_items_to_invoice(invoice, [te.id], [])
        te.refresh_from_db()
        line_id = te.invoice_line.id

        detach_invoice_lines(invoice, [line_id])

        # Verify
        te.refresh_from_db()
        assert te.status == BillableStatus.UNBILLED
        assert te.invoice_line is None
        assert invoice.lines.count() == 0

    def test_detach_expense_line(self, db):
        """Test detaching an expense line from invoice."""
        client = ClientFactory()
        category = ExpenseCategoryFactory()

        expense = ExpenseFactory(
            client=client,
            category=category,
            amount=Decimal("200.00"),
        )

        invoice = Invoice.objects.create(
            client=client,
            invoice_number="2025-DETACH2",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
        )

        attach_unbilled_items_to_invoice(invoice, [], [expense.id])
        expense.refresh_from_db()
        line_id = expense.invoice_line.id

        detach_invoice_lines(invoice, [line_id])

        expense.refresh_from_db()
        assert expense.status == BillableStatus.UNBILLED
        assert expense.invoice_line is None


# =============================================================================
# Invoice Void/Revert Tests
# =============================================================================

class TestMarkItemsUnbilled:
    def test_mark_all_unbilled_and_unlink(self, db):
        """Test voiding behavior - items become unbilled and unlinked."""
        client = ClientFactory()
        consultant = ConsultantFactory()
        category = ExpenseCategoryFactory()

        te = TimeEntryFactory(client=client, consultant=consultant)
        expense = ExpenseFactory(client=client, category=category)

        invoice = Invoice.objects.create(
            client=client,
            invoice_number="2025-VOID1",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
        )

        attach_unbilled_items_to_invoice(invoice, [te.id], [expense.id])

        # Now void
        mark_all_te_ex_unbilled_and_unlink(invoice)

        te.refresh_from_db()
        expense.refresh_from_db()

        assert te.status == BillableStatus.UNBILLED
        assert te.invoice_line is None
        assert expense.status == BillableStatus.UNBILLED
        assert expense.invoice_line is None

        # Lines should still exist (preserved for history)
        assert invoice.lines.count() == 2

    def test_mark_unbilled_keep_invoice_lines(self, db):
        """Test revert to draft - items unbilled but links preserved."""
        client = ClientFactory()
        consultant = ConsultantFactory()

        te = TimeEntryFactory(client=client, consultant=consultant)

        invoice = Invoice.objects.create(
            client=client,
            invoice_number="2025-REVERT1",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
        )

        attach_unbilled_items_to_invoice(invoice, [te.id], [])
        te.refresh_from_db()
        original_line = te.invoice_line

        # Mark as issued then revert
        te.status = BillableStatus.BILLED
        te.save()

        mark_te_ex_unbilled_keep_invoice_lines(invoice)

        te.refresh_from_db()
        assert te.status == BillableStatus.UNBILLED
        assert te.invoice_line == original_line  # Link preserved
