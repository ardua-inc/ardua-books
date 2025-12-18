"""
Management command to fix bank account balance discrepancies.

This command:
1. Deletes orphaned duplicate journal entries from bank transactions
2. Creates missing opening balance journal entries for bank accounts

Run with: python manage.py fix_bank_balances
Use --dry-run to preview changes without applying them.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from accounting.models import (
    BankAccount,
    BankTransaction,
    JournalEntry,
    JournalLine,
    ChartOfAccount,
    AccountType,
)


class Command(BaseCommand):
    help = "Fix bank account balance discrepancies by cleaning up duplicate JEs and creating missing opening balance JEs"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without applying them",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes will be made\n"))

        self.fix_orphaned_journal_entries(dry_run)
        self.create_missing_opening_balance_jes(dry_run)
        self.verify_balances()

    def fix_orphaned_journal_entries(self, dry_run):
        """Find and delete orphaned JEs that were not properly cleaned up."""
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Step 1: Finding orphaned journal entries ==="))

        ct_txn = ContentType.objects.get_for_model(BankTransaction)
        orphaned_jes = []

        for txn in BankTransaction.objects.filter(journal_entry__isnull=False):
            # Find all JEs that reference this transaction
            jes_for_txn = JournalEntry.objects.filter(
                source_content_type=ct_txn,
                source_object_id=txn.id
            )
            for je in jes_for_txn:
                if je.id != txn.journal_entry_id:
                    orphaned_jes.append(je)
                    self.stdout.write(f"  Found orphaned JE #{je.id}: {je.description[:50]}")

        self.stdout.write(f"\nFound {len(orphaned_jes)} orphaned JEs")

        if orphaned_jes and not dry_run:
            self.stdout.write(self.style.MIGRATE_HEADING("\nDeleting orphaned journal entries..."))
            with transaction.atomic():
                for je in orphaned_jes:
                    self.stdout.write(f"  Deleting JE #{je.id}")
                    je.delete()
            self.stdout.write(self.style.SUCCESS(f"Deleted {len(orphaned_jes)} orphaned JEs"))
        elif orphaned_jes and dry_run:
            self.stdout.write(self.style.WARNING(f"Would delete {len(orphaned_jes)} orphaned JEs"))

    def create_missing_opening_balance_jes(self, dry_run):
        """Create opening balance JEs for bank accounts that don't have them."""
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Step 2: Creating missing opening balance JEs ==="))

        ct_ba = ContentType.objects.get_for_model(BankAccount)
        owner_equity = ChartOfAccount.objects.get(code="3000")

        for ba in BankAccount.objects.all():
            if ba.opening_balance == 0:
                self.stdout.write(f"  {ba.institution}: opening balance is 0, skipping")
                continue

            # Check if opening JE exists
            has_opening_je = JournalEntry.objects.filter(
                source_content_type=ct_ba,
                source_object_id=ba.id
            ).exists()

            if has_opening_je:
                self.stdout.write(f"  {ba.institution}: already has opening JE, skipping")
                continue

            if dry_run:
                self.stdout.write(self.style.WARNING(
                    f"  {ba.institution}: would create opening JE for ${ba.opening_balance}"
                ))
                continue

            # Create opening balance JE
            with transaction.atomic():
                je = JournalEntry.objects.create(
                    description=f"Opening balance for {ba}",
                    posted_by=None,
                    source_content_type=ct_ba,
                    source_object_id=ba.id,
                )

                is_asset = ba.account.type == AccountType.ASSET
                ob = ba.opening_balance

                if is_asset:
                    if ob > 0:
                        # DR Bank, CR Equity
                        JournalLine.objects.create(entry=je, account=ba.account, debit=ob, credit=0)
                        JournalLine.objects.create(entry=je, account=owner_equity, debit=0, credit=ob)
                    else:
                        # DR Equity, CR Bank
                        JournalLine.objects.create(entry=je, account=owner_equity, debit=abs(ob), credit=0)
                        JournalLine.objects.create(entry=je, account=ba.account, debit=0, credit=abs(ob))
                else:
                    # Liability (credit card)
                    JournalLine.objects.create(entry=je, account=owner_equity, debit=ob, credit=0)
                    JournalLine.objects.create(entry=je, account=ba.account, debit=0, credit=ob)

                self.stdout.write(self.style.SUCCESS(
                    f"  {ba.institution}: created opening JE #{je.id} for ${ob}"
                ))

    def verify_balances(self):
        """Display current bank account balances for verification."""
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Verification: Current Bank Account Balances ==="))

        for ba in BankAccount.objects.all():
            self.stdout.write(f"  {ba.institution}: ${ba.balance:,.2f}")

        self.stdout.write(self.style.SUCCESS("\nDone!"))
