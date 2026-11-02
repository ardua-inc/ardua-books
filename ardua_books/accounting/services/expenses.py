"""
Posting for expenses paid outside the business bank and card accounts.

An expense paid from a bank or card account reaches the GL when its bank
transaction is linked (BankTransactionService.link_expense). One paid any other
way -- by the owner personally, from a gift-card balance, by a family member's
card the owner reimburses -- names a funding_account instead, and is posted:

    DR the category's expense account / CR funding_account   (a purchase)
    DR funding_account / CR the category's expense account   (a refund)

An expense is funded or linked, never both, so it is posted exactly once. A
purchase paid partly by card and partly otherwise is two expenses: one linked
to the card charge, one funded.

Expense.save() calls sync_funding_entry, and deleting an expense calls
remove_from_ledger, so the GL follows the expense on every path (views, the
mobile app, admin).
"""

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from accounting import gl_codes
from accounting.models import AccountType, JournalEntry
from accounting.services.banking import BankTransactionService, non_bank_accounts

# Expense fields the funding entry is built from; a save that touches none of
# them (link_expense saves payment_account only) leaves the entry alone.
FUNDING_FIELDS = {"funding_account", "category", "amount", "expense_date", "description"}


class ExpenseLedgerService:

    @staticmethod
    def funding_accounts():
        """
        Accounts an expense may be funded from: liabilities and equity (2500
        Loan from Shareholder, 3000 Owner Equity), less bank and card accounts
        (an expense paid from one of those is linked to its transaction) and
        2200 Unapplied Payments, which only payments use.
        """
        return (
            non_bank_accounts()
            .filter(type__in=[AccountType.LIABILITY, AccountType.EQUITY])
            .exclude(code=gl_codes.UNAPPLIED_PAYMENTS)
        )

    @staticmethod
    def funding_problem(expense):
        """Why expense cannot be funded as it stands, or None."""
        if expense.funding_account_id is None:
            return None
        if expense.pk and expense.bank_transactions.exists():
            return (
                "This expense is linked to a bank transaction, which already posts it; "
                "it cannot also be funded from another account."
            )
        if not ExpenseLedgerService.funding_accounts().filter(pk=expense.funding_account_id).exists():
            return (
                f"{expense.funding_account} cannot fund an expense; choose a liability or "
                "equity account such as Loan from Shareholder."
            )
        if expense.category.account_id is None:
            return (
                f"Expense category '{expense.category.name}' has no GL account assigned; "
                "assign one before recording a funded expense."
            )
        if not expense.amount:
            return "A funded expense needs a non-zero amount."
        return None

    @staticmethod
    def funding_entry_lines(expense):
        amt = abs(expense.amount)
        zero = Decimal("0")
        expense_account = expense.category.account
        funding = expense.funding_account
        if expense.amount > 0:
            return [(expense_account, amt, zero), (funding, zero, amt)]
        return [(funding, amt, zero), (expense_account, zero, amt)]

    @staticmethod
    def sync_funding_entry(expense, update_fields=None):
        """
        Make expense's funding JE match the expense: create it, rebuild it in
        place, or delete it once the funding account is cleared.
        """
        from billing.models import Expense

        if update_fields is not None and not FUNDING_FIELDS & set(update_fields):
            return
        if expense.funding_account_id is None and expense.funding_entry_id is None:
            return

        je = JournalEntry.objects.filter(pk=expense.funding_entry_id).first() if expense.funding_entry_id else None

        if expense.funding_account_id is None:
            if je is not None:
                je.delete()
            Expense.objects.filter(pk=expense.pk).update(funding_entry=None)
            expense.funding_entry = None
            return

        problem = ExpenseLedgerService.funding_problem(expense)
        if problem:
            raise ValueError(problem)

        description = f"Expense (funded): {expense.description or expense.category.name}"[:255]
        wanted = ExpenseLedgerService.funding_entry_lines(expense)

        if je is None:
            je = JournalEntry.objects.create(
                posted_at=expense.expense_date,
                description=description,
                source_content_type=ContentType.objects.get_for_model(Expense),
                source_object_id=expense.id,
            )
            BankTransactionService.create_lines(je, wanted)
            Expense.objects.filter(pk=expense.pk).update(funding_entry=je)
            expense.funding_entry = je
            return

        if timezone.localdate(je.posted_at) != expense.expense_date or je.description != description:
            je.posted_at = expense.expense_date
            je.description = description
            je.save(update_fields=["posted_at", "description"])
        current = sorted((l.account_id, l.debit, l.credit) for l in je.lines.all())
        if current != sorted((a.id, d, c) for a, d, c in wanted):
            je.lines.all().delete()
            BankTransactionService.create_lines(je, wanted)

    @staticmethod
    def remove_from_ledger(expense):
        """
        Before an expense is deleted: unmatch any bank transaction linked to
        it (the transaction returns to unmatched on Owner Equity, its import
        entry restored) and delete its funding JE. Leaves no JE behind that
        points at the deleted expense.
        """
        for txn in expense.bank_transactions.all():
            BankTransactionService.unmatch(txn)
        if expense.funding_entry_id:
            JournalEntry.objects.filter(pk=expense.funding_entry_id).delete()
