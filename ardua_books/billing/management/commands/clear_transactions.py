"""
Clear transactional data from the database while preserving configuration.

Preserves:
- Bank accounts & import profiles
- Chart of accounts (GL accounts)
- Users & groups
- Consultants
- Expense categories
- Clients

Deletes:
- Journal entries & lines
- Time entries
- Expenses
- Invoices & invoice lines
- Payments & payment applications
- Bank transactions

Usage:
    python manage.py clear_transactions
    python manage.py clear_transactions --yes  # Skip confirmation
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Clear transactional data while preserving configuration"

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip confirmation prompt",
        )

    def handle(self, *args, **options):
        from billing.models import TimeEntry, Expense, Invoice, InvoiceLine
        from accounting.models import (
            JournalEntry,
            JournalLine,
            Payment,
            PaymentApplication,
            BankTransaction,
        )

        # Count records to be deleted
        counts = {
            "JournalLine": JournalLine.objects.count(),
            "JournalEntry": JournalEntry.objects.count(),
            "PaymentApplication": PaymentApplication.objects.count(),
            "Payment": Payment.objects.count(),
            "InvoiceLine": InvoiceLine.objects.count(),
            "Invoice": Invoice.objects.count(),
            "TimeEntry": TimeEntry.objects.count(),
            "Expense": Expense.objects.count(),
            "BankTransaction": BankTransaction.objects.count(),
        }

        total = sum(counts.values())

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Database is already clean."))
            return

        self.stdout.write(self.style.WARNING("\nRecords to be deleted:"))
        self.stdout.write("-" * 40)
        for model, count in counts.items():
            if count > 0:
                self.stdout.write(f"  {model}: {count}")
        self.stdout.write("-" * 40)
        self.stdout.write(f"  Total: {total}")
        self.stdout.write("")

        if not options["yes"]:
            confirm = input("Are you sure you want to delete all this data? [y/N]: ")
            if confirm.lower() != "y":
                self.stdout.write(self.style.WARNING("Cancelled."))
                return

        self.stdout.write("Deleting records...")

        with transaction.atomic():
            # Delete in order to respect foreign key constraints

            # 1. Journal lines first (FK to JournalEntry)
            deleted = JournalLine.objects.all().delete()[0]
            self.stdout.write(f"  Deleted {deleted} JournalLine records")

            # 2. Journal entries
            deleted = JournalEntry.objects.all().delete()[0]
            self.stdout.write(f"  Deleted {deleted} JournalEntry records")

            # 3. Payment applications (FK to Payment and Invoice)
            deleted = PaymentApplication.objects.all().delete()[0]
            self.stdout.write(f"  Deleted {deleted} PaymentApplication records")

            # 4. Bank transactions (FK to Payment) - clear payment link first
            BankTransaction.objects.update(payment=None, expense=None)
            deleted = BankTransaction.objects.all().delete()[0]
            self.stdout.write(f"  Deleted {deleted} BankTransaction records")

            # 5. Payments
            deleted = Payment.objects.all().delete()[0]
            self.stdout.write(f"  Deleted {deleted} Payment records")

            # 6. Time entries (clear invoice_line FK first)
            TimeEntry.objects.update(invoice_line=None)

            # 7. Expenses (clear invoice_line FK first)
            Expense.objects.update(invoice_line=None)

            # 8. Invoice lines
            deleted = InvoiceLine.objects.all().delete()[0]
            self.stdout.write(f"  Deleted {deleted} InvoiceLine records")

            # 9. Invoices
            deleted = Invoice.objects.all().delete()[0]
            self.stdout.write(f"  Deleted {deleted} Invoice records")

            # 10. Time entries
            deleted = TimeEntry.objects.all().delete()[0]
            self.stdout.write(f"  Deleted {deleted} TimeEntry records")

            # 11. Expenses
            deleted = Expense.objects.all().delete()[0]
            self.stdout.write(f"  Deleted {deleted} Expense records")

        self.stdout.write(self.style.SUCCESS("\nDatabase cleared successfully."))
