"""
Management command to reset accounting data for a fresh start.

This command clears:
- Bank Transactions
- Journal Entries (and lines)
- Payments (and applications)
- Invoices (and lines)
- Time Entries
- Expenses

It preserves:
- Clients, Consultants
- Chart of Accounts, Bank Accounts, Import Profiles
- Expense Categories

Run with: python manage.py reset_accounting_data
Use --dry-run to preview what will be deleted.
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Reset accounting data for a fresh start (clears transactions, JEs, payments, invoices, time entries, expenses)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what will be deleted without making changes",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required flag to confirm you want to delete data",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        confirm = options["confirm"]

        if not dry_run and not confirm:
            self.stdout.write(self.style.ERROR(
                "This command will DELETE data. Use --dry-run to preview, "
                "or --confirm to proceed."
            ))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes will be made\n"))

        self.show_current_counts()

        if not dry_run:
            self.reset_data()
            self.stdout.write(self.style.SUCCESS("\n=== Data reset complete ==="))
            self.show_current_counts()

    def show_current_counts(self):
        from accounting.models import BankTransaction, JournalEntry, Payment
        from billing.models import Invoice, TimeEntry, Expense, BillableStatus

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Current Data Counts ==="))
        self.stdout.write(f"  Bank Transactions: {BankTransaction.objects.count()}")
        self.stdout.write(f"  Journal Entries: {JournalEntry.objects.count()}")
        self.stdout.write(f"  Payments: {Payment.objects.count()}")
        self.stdout.write(f"  Invoices: {Invoice.objects.count()}")
        self.stdout.write(f"  Time Entries: {TimeEntry.objects.count()} "
                         f"({TimeEntry.objects.filter(status=BillableStatus.BILLED).count()} billed)")
        self.stdout.write(f"  Expenses: {Expense.objects.count()} "
                         f"({Expense.objects.filter(status=BillableStatus.BILLED).count()} billed)")

    @transaction.atomic
    def reset_data(self):
        from accounting.models import BankTransaction, JournalEntry, Payment
        from billing.models import Invoice, TimeEntry, Expense

        # Step 1: Clear bank transactions (must be before payments due to FK)
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Step 1: Clearing bank transactions ==="))
        bt_count = BankTransaction.objects.count()
        BankTransaction.objects.all().delete()
        self.stdout.write(f"  Deleted {bt_count} bank transactions")

        # Step 2: Clear time entries
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Step 2: Clearing time entries ==="))
        te_count = TimeEntry.objects.count()
        TimeEntry.objects.all().delete()
        self.stdout.write(f"  Deleted {te_count} time entries")

        # Step 3: Clear expenses
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Step 3: Clearing expenses ==="))
        ex_count = Expense.objects.count()
        Expense.objects.all().delete()
        self.stdout.write(f"  Deleted {ex_count} expenses")

        # Step 4: Clear invoices (cascade deletes invoice lines)
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Step 4: Clearing invoices ==="))
        inv_count = Invoice.objects.count()
        Invoice.objects.all().delete()
        self.stdout.write(f"  Deleted {inv_count} invoices")

        # Step 5: Clear payments (cascade deletes payment applications)
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Step 5: Clearing payments ==="))
        pay_count = Payment.objects.count()
        Payment.objects.all().delete()
        self.stdout.write(f"  Deleted {pay_count} payments")

        # Step 6: Clear journal entries (cascade deletes journal lines)
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Step 6: Clearing journal entries ==="))
        je_count = JournalEntry.objects.count()
        JournalEntry.objects.all().delete()
        self.stdout.write(f"  Deleted {je_count} journal entries")
