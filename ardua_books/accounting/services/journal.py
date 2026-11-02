"""
Manual journal entries: adjustments and reclassifications that no payment,
invoice, expense or bank transaction posts (e.g. moving an amount from 3000
Owner Equity to 2500 Loan from Shareholder).

A manual entry is never edited or deleted; it is corrected by reversing it
(a new manual entry with every line's sides swapped) and entering it again.
"""

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from accounting.models import JournalEntry
from accounting.services.banking import BankTransactionService, non_bank_accounts


class JournalEntryService:

    @staticmethod
    def manual_entry_accounts():
        """
        Accounts a manual entry may use: the active chart less bank and card
        accounts, which move only with their registers (record bank activity
        by importing it, then recategorize or match it).
        """
        return non_bank_accounts()

    @staticmethod
    @transaction.atomic
    def create_manual_entry(posted_on, memo, lines, user=None):
        """
        lines: (account, debit, credit) tuples. Lines with neither a debit nor
        a credit are ignored; of the rest there must be at least two, each on
        one side only, and debits must equal credits.
        """
        lines = [(a, Decimal(d or 0), Decimal(c or 0)) for a, d, c in lines if (d or 0) or (c or 0)]
        JournalEntryService.check_lines(lines)
        je = JournalEntry.objects.create(
            posted_at=posted_on, posted_by=user, description=memo, is_manual=True,
        )
        BankTransactionService.create_lines(je, lines)
        return je

    @staticmethod
    def check_lines(lines):
        if len(lines) < 2:
            raise ValueError("A journal entry needs at least two lines with amounts.")
        allowed = set(JournalEntryService.manual_entry_accounts().values_list("pk", flat=True))
        for account, debit, credit in lines:
            if account.pk not in allowed:
                raise ValueError(
                    f"{account} cannot be used in a manual entry (bank and card accounts, "
                    "and inactive accounts, are excluded)."
                )
            if debit < 0 or credit < 0:
                raise ValueError(f"{account}: amounts cannot be negative.")
            if debit and credit:
                raise ValueError(f"{account}: a line is either a debit or a credit, not both.")
        debits = sum(d for _, d, _ in lines)
        credits = sum(c for _, _, c in lines)
        if debits != credits:
            raise ValueError(f"Debits ({debits}) do not equal credits ({credits}).")

    @staticmethod
    @transaction.atomic
    def reverse_entry(je, posted_on, user=None):
        """Post the reversal of manual entry je on posted_on."""
        je = JournalEntry.objects.select_for_update().get(pk=je.pk)
        if not je.is_manual:
            raise ValueError(
                "Only manual entries can be reversed here. Entries posted by a payment, invoice, "
                "expense or bank transaction are corrected through that record."
            )
        if je.reverses is not None:
            raise ValueError(f"JE #{je.id} is itself a reversal (of JE #{je.reverses.id}).")
        if je.reversed_by is not None:
            raise ValueError(f"JE #{je.id} has already been reversed by JE #{je.reversed_by.id}.")
        reversal = JournalEntry.objects.create(
            posted_at=posted_on,
            posted_by=user,
            description=f"Reversal of JE #{je.id}: {je.description}"[:255],
            is_manual=True,
            source_content_type=ContentType.objects.get_for_model(JournalEntry),
            source_object_id=je.id,
        )
        BankTransactionService.create_lines(
            reversal, [(l.account, l.credit, l.debit) for l in je.lines.select_related("account")]
        )
        return reversal
