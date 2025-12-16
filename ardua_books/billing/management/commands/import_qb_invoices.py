"""
Import QuickBooks "Sales by Customer Detail" CSV into Ardua Books.

Usage:
    python manage.py import_qb_invoices path/to/file.csv \
        --income-account 4000 \
        --consultant 1 \
        --expense-category "Equipment"

This will:
1. Parse the QuickBooks CSV export
2. Create TimeEntry records (for JMR-* items) and Expense records (for EXP items)
3. Create Invoices with InvoiceLine records linked to time/expense
4. Mark invoices as PAID with Payment records
5. Create all necessary Journal Entries
"""
import csv
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.contrib.contenttypes.models import ContentType

from billing.models import (
    Client,
    Consultant,
    TimeEntry,
    Expense,
    ExpenseCategory,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    BillableStatus,
)
from accounting.models import (
    ChartOfAccount,
    JournalEntry,
    JournalLine,
    Payment,
    PaymentApplication,
    PaymentMethod,
)


class Command(BaseCommand):
    help = "Import QuickBooks Sales by Customer Detail CSV"

    def add_arguments(self, parser):
        parser.add_argument("csv_file", help="Path to the QuickBooks CSV export")
        parser.add_argument(
            "--income-account",
            required=True,
            help="GL account code for revenue credit (e.g., 4000)",
        )
        parser.add_argument(
            "--consultant",
            required=True,
            help="Consultant ID for time entries",
        )
        parser.add_argument(
            "--expense-category",
            default="Equipment",
            help="Expense category name for imported expenses (default: Equipment)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be imported without making changes",
        )

    def handle(self, *args, **options):
        csv_file = options["csv_file"]
        income_account_code = options["income_account"]
        consultant_id = options["consultant"]
        expense_category_name = options["expense_category"]
        dry_run = options["dry_run"]

        # Validate inputs
        try:
            income_account = ChartOfAccount.objects.get(code=income_account_code)
        except ChartOfAccount.DoesNotExist:
            raise CommandError(f"Income account '{income_account_code}' not found")

        try:
            consultant = Consultant.objects.get(pk=consultant_id)
        except Consultant.DoesNotExist:
            raise CommandError(f"Consultant with ID {consultant_id} not found")

        try:
            expense_category = ExpenseCategory.objects.get(name=expense_category_name)
        except ExpenseCategory.DoesNotExist:
            raise CommandError(
                f"Expense category '{expense_category_name}' not found. "
                f"Available: {', '.join(ExpenseCategory.objects.values_list('name', flat=True))}"
            )

        # Get required GL accounts
        try:
            ar_account = ChartOfAccount.objects.get(code="1100")
        except ChartOfAccount.DoesNotExist:
            raise CommandError("Accounts Receivable account (1100) not found")

        try:
            cash_account = ChartOfAccount.objects.get(code="1000")
        except ChartOfAccount.DoesNotExist:
            raise CommandError("Cash account (1000) not found")

        # Parse CSV
        invoices_data = self.parse_csv(csv_file)

        if not invoices_data:
            self.stdout.write(self.style.WARNING("No invoice data found in CSV"))
            return

        # Get client name from first invoice
        client_name = invoices_data[0]["client_name"]
        try:
            client = Client.objects.get(name=client_name)
        except Client.DoesNotExist:
            raise CommandError(
                f"Client '{client_name}' not found. Please create the client first."
            )

        # Preview
        self.stdout.write(self.style.SUCCESS(f"\n{'='*60}"))
        self.stdout.write(self.style.SUCCESS(f"Import Preview"))
        self.stdout.write(self.style.SUCCESS(f"{'='*60}"))
        self.stdout.write(f"Client: {client.name}")
        self.stdout.write(f"Consultant: {consultant.display_name}")
        self.stdout.write(f"Income Account: {income_account}")
        self.stdout.write(f"Expense Category: {expense_category.name}")
        self.stdout.write(f"Invoices to import: {len(invoices_data)}")
        self.stdout.write("")

        total_amount = Decimal("0")
        for inv_data in invoices_data:
            time_count = sum(1 for line in inv_data["lines"] if line["is_time"])
            exp_count = sum(1 for line in inv_data["lines"] if not line["is_time"])
            inv_total = sum(line["amount"] for line in inv_data["lines"])
            total_amount += inv_total

            self.stdout.write(
                f"  Invoice #{inv_data['invoice_number']} ({inv_data['invoice_date']}): "
                f"{time_count} time entries, {exp_count} expenses, "
                f"Total: ${inv_total:,.2f}"
            )

        self.stdout.write("")
        self.stdout.write(f"Grand Total: ${total_amount:,.2f}")
        self.stdout.write("")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes made"))
            return

        # Confirm
        confirm = input("Proceed with import? [y/N]: ")
        if confirm.lower() != "y":
            self.stdout.write(self.style.WARNING("Import cancelled"))
            return

        # Run import
        with transaction.atomic():
            self.run_import(
                invoices_data,
                client,
                consultant,
                expense_category,
                income_account,
                ar_account,
                cash_account,
            )

        self.stdout.write(self.style.SUCCESS(f"\nImport complete!"))

    def parse_csv(self, csv_file):
        """Parse QuickBooks Sales by Customer Detail CSV."""
        invoices = {}
        client_name = None

        with open(csv_file, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Find the header row (contains "Type", "Date", "Num", etc.)
        header_idx = None
        for i, row in enumerate(rows):
            if len(row) >= 4 and "Type" in row and "Date" in row and "Num" in row:
                header_idx = i
                break

        if header_idx is None:
            raise CommandError("Could not find header row in CSV")

        # Map column names to indices
        header = rows[header_idx]
        col_map = {name.strip(): idx for idx, name in enumerate(header) if name.strip()}

        required_cols = ["Type", "Date", "Num", "Name", "Memo", "Item", "Qty", "Sales Price", "Amount"]
        for col in required_cols:
            if col not in col_map:
                raise CommandError(f"Required column '{col}' not found in CSV")

        # Parse data rows
        for row in rows[header_idx + 1:]:
            if len(row) < len(header):
                continue

            row_type = row[col_map["Type"]].strip()
            if row_type != "Invoice":
                continue

            invoice_num = row[col_map["Num"]].strip()
            if not invoice_num:
                continue

            # Get client name from first invoice row
            name = row[col_map["Name"]].strip()
            if name and not client_name:
                client_name = name

            # Parse date (mm/dd/yy format)
            date_str = row[col_map["Date"]].strip()
            try:
                invoice_date = datetime.strptime(date_str, "%m/%d/%y").date()
            except ValueError:
                self.stdout.write(
                    self.style.WARNING(f"Could not parse date '{date_str}', skipping row")
                )
                continue

            # Parse line item
            memo = row[col_map["Memo"]].strip()
            item = row[col_map["Item"]].strip()

            # Parse quantity
            qty_str = row[col_map["Qty"]].strip()
            try:
                qty = Decimal(qty_str) if qty_str else Decimal("1")
            except InvalidOperation:
                qty = Decimal("1")

            # Parse unit price
            price_str = row[col_map["Sales Price"]].strip().replace(",", "")
            try:
                unit_price = Decimal(price_str) if price_str else Decimal("0")
            except InvalidOperation:
                unit_price = Decimal("0")

            # Parse amount
            amount_str = row[col_map["Amount"]].strip().replace(",", "")
            try:
                amount = Decimal(amount_str) if amount_str else Decimal("0")
            except InvalidOperation:
                amount = Decimal("0")

            # Determine if time entry (JMR-*) or expense
            is_time = item.upper().startswith("JMR")

            # Parse work date from memo (format: "mm/dd/yy - description")
            work_date = None
            description = memo
            date_match = re.match(r"(\d{2}/\d{2}/\d{2})\s*-\s*(.+)", memo)
            if date_match:
                try:
                    work_date = datetime.strptime(date_match.group(1), "%m/%d/%y").date()
                    description = date_match.group(2).strip()
                except ValueError:
                    pass

            # If no work date parsed, use invoice date
            if work_date is None:
                work_date = invoice_date

            # Build invoice data structure
            if invoice_num not in invoices:
                invoices[invoice_num] = {
                    "invoice_number": invoice_num,
                    "invoice_date": invoice_date,
                    "client_name": client_name,
                    "lines": [],
                }

            invoices[invoice_num]["lines"].append({
                "is_time": is_time,
                "work_date": work_date,
                "description": description,
                "quantity": qty,
                "unit_price": unit_price,
                "amount": amount,
                "item_code": item,
            })

        return list(invoices.values())

    def run_import(
        self,
        invoices_data,
        client,
        consultant,
        expense_category,
        income_account,
        ar_account,
        cash_account,
    ):
        """Execute the import."""
        for inv_data in invoices_data:
            self.stdout.write(f"  Importing Invoice #{inv_data['invoice_number']}...")

            # Check if invoice already exists
            if Invoice.objects.filter(invoice_number=inv_data["invoice_number"]).exists():
                self.stdout.write(
                    self.style.WARNING(
                        f"    Invoice #{inv_data['invoice_number']} already exists, skipping"
                    )
                )
                continue

            # Create invoice
            invoice = Invoice.objects.create(
                client=client,
                invoice_number=inv_data["invoice_number"],
                issue_date=inv_data["invoice_date"],
                due_date=inv_data["invoice_date"] + timedelta(days=client.payment_terms_days),
                status=InvoiceStatus.ISSUED,  # Will change to PAID after payment
            )

            # Create time entries, expenses, and invoice lines
            for line_data in inv_data["lines"]:
                if line_data["is_time"]:
                    # Create TimeEntry
                    time_entry = TimeEntry.objects.create(
                        client=client,
                        consultant=consultant,
                        work_date=line_data["work_date"],
                        hours=line_data["quantity"],
                        description=line_data["description"],
                        billing_rate=line_data["unit_price"],
                        status=BillableStatus.BILLED,
                    )

                    # Create InvoiceLine for time
                    invoice_line = InvoiceLine.objects.create(
                        invoice=invoice,
                        line_type=InvoiceLine.LineType.TIME,
                        description=f"{line_data['work_date']} {line_data['description']}",
                        quantity=line_data["quantity"],
                        unit_price=line_data["unit_price"],
                    )

                    # Link time entry to invoice line
                    time_entry.invoice_line = invoice_line
                    time_entry.save()

                else:
                    # Create Expense
                    expense = Expense.objects.create(
                        client=client,
                        category=expense_category,
                        expense_date=line_data["work_date"],
                        amount=line_data["amount"],
                        description=line_data["description"],
                        billable=True,
                        status=BillableStatus.BILLED,
                    )

                    # Create InvoiceLine for expense
                    invoice_line = InvoiceLine.objects.create(
                        invoice=invoice,
                        line_type=InvoiceLine.LineType.EXPENSE,
                        description=f"{line_data['work_date']} {line_data['description']}",
                        quantity=1,
                        unit_price=line_data["amount"],
                    )

                    # Link expense to invoice line
                    expense.invoice_line = invoice_line
                    expense.save()

            # Recalculate invoice totals
            invoice.recalculate_totals()

            # Create Journal Entry for invoice (DR AR, CR Revenue)
            ct_invoice = ContentType.objects.get_for_model(Invoice)
            je_invoice = JournalEntry.objects.create(
                posted_at=inv_data["invoice_date"],
                description=f"Invoice {invoice.invoice_number} posted (imported)",
                source_content_type=ct_invoice,
                source_object_id=invoice.id,
            )
            JournalLine.objects.create(
                entry=je_invoice,
                account=ar_account,
                debit=invoice.total,
                credit=Decimal("0"),
            )
            JournalLine.objects.create(
                entry=je_invoice,
                account=income_account,
                debit=Decimal("0"),
                credit=invoice.total,
            )

            # Create Payment
            payment = Payment.objects.create(
                client=client,
                date=inv_data["invoice_date"],
                amount=invoice.total,
                method=PaymentMethod.CHECK,
                memo=f"Payment for Invoice {invoice.invoice_number} (imported)",
                unapplied_amount=Decimal("0"),
            )

            # Create PaymentApplication
            PaymentApplication.objects.create(
                payment=payment,
                invoice=invoice,
                amount=invoice.total,
            )

            # Create Journal Entry for payment (DR Cash, CR AR)
            ct_payment = ContentType.objects.get_for_model(Payment)
            je_payment = JournalEntry.objects.create(
                posted_at=inv_data["invoice_date"],
                description=f"Payment received from {client.name} (imported)",
                source_content_type=ct_payment,
                source_object_id=payment.id,
            )
            JournalLine.objects.create(
                entry=je_payment,
                account=cash_account,
                debit=invoice.total,
                credit=Decimal("0"),
            )
            JournalLine.objects.create(
                entry=je_payment,
                account=ar_account,
                debit=Decimal("0"),
                credit=invoice.total,
            )

            # Update invoice status to PAID
            invoice.status = InvoiceStatus.PAID
            invoice.save()

            self.stdout.write(
                self.style.SUCCESS(
                    f"    Created: {len(inv_data['lines'])} line items, "
                    f"total ${invoice.total:,.2f}"
                )
            )
