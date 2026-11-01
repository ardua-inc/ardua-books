"""
Repair journal entries posted wrongly by the four ledger bugs fixed in 3.3.4.

Dry run by default: prints every change it would make and the trial balance
and bank-register reconciliation before and after, and writes nothing. On
PostgreSQL the dry run runs in a READ ONLY transaction, so it works under the
books_readonly login against live data.

    python manage.py repair_ledger_postings            # dry run
    python manage.py repair_ledger_postings --apply    # make the changes

--apply makes every change in one transaction, then recomputes the trial
balance from the database. If that differs from the dry run's prediction, or
any bank register still differs from its GL account, everything is rolled
back. Back up the database first all the same.

What it repairs. Problems are found from the data, not from fixed ids, so a
second run finds nothing to do:

1. Payments linked to bank deposits. The deposit's import JE (DR bank / CR
   offset) was never deleted, and the payment's JE debited 1000 Cash, so the
   cash was counted twice. Delete the import JE; move the payment JE's Cash
   debit to the deposit's GL account.
2. Payments credited AR 1200 while invoices debit AR 1100. Move those credits
   to 1100; deactivate 1200 once nothing is posted to it.
3. Credits (refunds) linked to expenses were posted as charges. Rebuild each
   JE as DR bank / CR expense, and make the expense negative (a refund).
4. Transfers that paired two withdrawals (or two deposits). Unpair them and
   restore each one's import JE against Owner Equity, the importer's default
   offset, so they can be matched correctly by hand.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Sum

from accounting import gl_codes
from accounting.models import (
    BankAccount,
    BankTransaction,
    ChartOfAccount,
    JournalEntry,
    JournalLine,
    Payment,
)
from accounting.services.banking import BankTransactionService

# The second "Accounts Receivable" that Payment.post_to_accounting credited
# before 3.3.4. Only this command still needs to know about it.
LEGACY_PAYMENT_AR = "1200"

ZERO = Decimal("0")
CENT = Decimal("0.01")


def _fmt_lines(lines):
    """[(account, debit, credit)] -> 'DR 1112 1397.41 / CR 3000 1397.41'"""
    parts = []
    for account, debit, credit in lines:
        if debit:
            parts.append(f"DR {account.code} {debit}")
        if credit:
            parts.append(f"CR {account.code} {credit}")
    return " / ".join(parts)


def _lines_of(je):
    return [(l.account, l.debit, l.credit) for l in je.lines.select_related("account").order_by("id")]


def _same_lines(a, b):
    key = lambda lines: sorted((acct.pk, debit, credit) for acct, debit, credit in lines)
    return key(a) == key(b)


# --- operations ---------------------------------------------------------------
# Each operation knows its effect on account balances (for the dry run) and how
# to make the change (for --apply). apply() re-reads rows by id, because an
# earlier operation may have changed them (deleting a JE nulls the
# BankTransaction.journal_entry that pointed at it).

@dataclass
class DeleteEntry:
    je: JournalEntry
    reason: str

    def deltas(self):
        return [(acct, -debit, -credit) for acct, debit, credit in _lines_of(self.je)]

    def describe(self):
        return f"JE {self.je.id}: delete ({_fmt_lines(_lines_of(self.je))}). {self.reason}"

    def apply(self):
        JournalEntry.objects.get(pk=self.je.pk).delete()


@dataclass
class MoveLine:
    line: JournalLine
    to_account: ChartOfAccount
    reason: str

    def deltas(self):
        l = self.line
        return [(l.account, -l.debit, -l.credit), (self.to_account, l.debit, l.credit)]

    def describe(self):
        l = self.line
        side = f"DR {l.debit}" if l.debit else f"CR {l.credit}"
        return (
            f"JE {l.entry_id}, line {l.id}: {side} moves from {l.account} "
            f"to {self.to_account}. {self.reason}"
        )

    def apply(self):
        JournalLine.objects.filter(pk=self.line.pk).update(account=self.to_account)


@dataclass
class ReplaceLines:
    je: JournalEntry
    new_lines: list
    reason: str

    def deltas(self):
        old = [(a, -d, -c) for a, d, c in _lines_of(self.je)]
        return old + list(self.new_lines)

    def describe(self):
        return (
            f"JE {self.je.id}: {_fmt_lines(_lines_of(self.je))} becomes "
            f"{_fmt_lines(self.new_lines)}. {self.reason}"
        )

    def apply(self):
        je = JournalEntry.objects.get(pk=self.je.pk)
        je.lines.all().delete()
        BankTransactionService.create_lines(je, self.new_lines)


@dataclass
class RestoreImportEntry:
    """Unpair a transfer half and re-post it as post_transaction() would."""

    txn: BankTransaction
    offset_account: ChartOfAccount
    reason: str

    def lines(self):
        return BankTransactionService.import_entry_lines(self.txn, self.offset_account)

    def deltas(self):
        return self.lines()

    def describe(self):
        t = self.txn
        return (
            f"Bank txn {t.id} ({t.bank_account.institution}, {t.date}, {t.amount}, "
            f"{t.description!r}): unpair from txn {t.transfer_pair_id}; new JE "
            f"{_fmt_lines(self.lines())}. {self.reason}"
        )

    def apply(self):
        txn = BankTransaction.objects.select_related("bank_account__account").get(pk=self.txn.pk)
        je = JournalEntry.objects.create(
            posted_at=txn.date,
            description=f"Bank txn: {txn.description}",
            source_content_type=ContentType.objects.get_for_model(BankTransaction),
            source_object_id=txn.id,
        )
        BankTransactionService.create_lines(je, self.lines())
        txn.journal_entry = je
        txn.offset_account = self.offset_account
        txn.transfer_pair = None
        txn.save(update_fields=["journal_entry", "offset_account", "transfer_pair"])


@dataclass
class SetField:
    obj: object
    field_name: str
    value: object
    reason: str

    def deltas(self):
        return []

    def describe(self):
        before = getattr(self.obj, self.field_name)
        return (
            f"{type(self.obj).__name__} {self.obj.pk}: {self.field_name} "
            f"{before} -> {self.value}. {self.reason}"
        )

    def apply(self):
        type(self.obj).objects.filter(pk=self.obj.pk).update(**{self.field_name: self.value})


# --- the repair plan -------------------------------------------------------------

@dataclass
class Step:
    title: str
    ops: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def plan_payment_deposits():
    """Bug 1: payment-linked deposits counted twice, payment debited Cash."""
    step = Step("1. Payments linked to bank deposits")
    ct_txn = ContentType.objects.get_for_model(BankTransaction)
    ct_payment = ContentType.objects.get_for_model(Payment)
    cash = ChartOfAccount.objects.filter(code=gl_codes.CASH).first()

    txns = (
        BankTransaction.objects.filter(payment__isnull=False)
        .select_related("payment", "bank_account__account")
        .order_by("id")
    )
    for txn in txns:
        payment = txn.payment
        label = f"payment {payment.id} / bank txn {txn.id}"
        if payment.bank_transactions.count() != 1:
            step.warnings.append(f"Skipped {label}: the payment is linked to more than one bank transaction.")
            continue
        if payment.amount != txn.amount:
            step.warnings.append(
                f"Skipped {label}: payment amount {payment.amount} differs from the deposit's {txn.amount}."
            )
            continue
        payment_je = JournalEntry.objects.filter(
            source_content_type=ct_payment, source_object_id=payment.id
        ).first()
        if payment_je is None:
            step.warnings.append(f"Skipped {label}: the payment has no journal entry.")
            continue

        for je in JournalEntry.objects.filter(source_content_type=ct_txn, source_object_id=txn.id).order_by("id"):
            step.ops.append(DeleteEntry(je, f"Import JE of bank txn {txn.id}, which {label} also records."))

        if cash is not None:
            for line in payment_je.lines.filter(account=cash, debit__gt=0).select_related("account"):
                step.ops.append(MoveLine(line, txn.bank_account.account, f"The money of {label} went to this account."))

        if txn.journal_entry_id != payment_je.id:
            step.ops.append(SetField(txn, "journal_entry", payment_je, "Point the deposit at its payment's JE."))
    return step


def plan_receivables():
    """Bug 2: payments credited AR 1200; invoices debit AR 1100."""
    step = Step(f"2. Accounts receivable: {LEGACY_PAYMENT_AR} -> {gl_codes.ACCOUNTS_RECEIVABLE}")
    legacy = ChartOfAccount.objects.filter(code=LEGACY_PAYMENT_AR).first()
    if legacy is None:
        return step
    ar = gl_codes.gl_account(gl_codes.ACCOUNTS_RECEIVABLE)
    ct_payment = ContentType.objects.get_for_model(Payment)

    remaining = 0
    for line in JournalLine.objects.filter(account=legacy).select_related("entry", "account").order_by("entry_id", "id"):
        if line.entry.source_content_type_id == ct_payment.id:
            step.ops.append(MoveLine(line, ar, f"Payment {line.entry.source_object_id} applied to invoices debited to {ar.code}."))
        else:
            remaining += 1
            step.warnings.append(
                f"JE {line.entry_id} ({line.entry.description!r}) posts to {legacy.code} but is not a payment; left alone."
            )
    if remaining == 0 and legacy.is_active:
        step.ops.append(SetField(legacy, "is_active", False, f"Nothing is posted to {legacy.code} any more."))
    return step


def plan_credits_linked_to_expenses():
    """Bug 3: refunds linked to expenses were posted as charges."""
    step = Step("3. Credits (refunds) linked to expenses")
    txns = (
        BankTransaction.objects.filter(expense__isnull=False, amount__gt=0)
        .select_related("expense", "bank_account__account", "journal_entry")
        .order_by("id")
    )
    for txn in txns:
        expense = txn.expense
        je = txn.journal_entry
        label = f"bank txn {txn.id} ({txn.date}, +{txn.amount}, {txn.description.strip()!r}) / expense {expense.id}"
        if je is None:
            step.warnings.append(f"Skipped {label}: no journal entry.")
            continue
        bank = txn.bank_account.account
        lines = _lines_of(je)
        other = [acct for acct, _, _ in lines if acct.pk != bank.pk]
        if len(lines) != 2 or len(other) != 1:
            step.warnings.append(f"Skipped {label}: JE {je.id} is not a two-line bank/expense entry.")
            continue
        expense_account = other[0]
        correct = BankTransactionService.expense_entry_lines(txn, expense_account)
        posted_as_charge = BankTransactionService.expense_entry_lines(
            BankTransaction(bank_account=txn.bank_account, amount=-txn.amount), expense_account
        )

        if _same_lines(lines, posted_as_charge):
            step.ops.append(ReplaceLines(je, correct, f"A refund reduces {expense_account}; {label}."))
        elif not _same_lines(lines, correct):
            step.warnings.append(f"Skipped {label}: JE {je.id} has unexpected lines ({_fmt_lines(lines)}).")
            continue

        if expense.amount != -txn.amount:
            if expense.invoice_line_id:
                step.warnings.append(f"Expense {expense.id} is on an invoice; its amount {expense.amount} was left alone.")
            else:
                step.ops.append(SetField(expense, "amount", -txn.amount, "A refund is a negative expense."))
    return step


def plan_same_sign_transfers():
    """Bug 4: a 'transfer' between two withdrawals (or two deposits)."""
    step = Step("4. Transfers that pair two withdrawals or two deposits")
    owner_equity = gl_codes.gl_account(gl_codes.OWNER_EQUITY)
    pairs = (
        BankTransaction.objects.filter(transfer_pair__isnull=False)
        .select_related("transfer_pair__bank_account__account", "bank_account__account", "journal_entry")
        .order_by("id")
    )
    for txn in pairs:
        other = txn.transfer_pair
        if txn.id > other.id or (txn.amount > 0) != (other.amount > 0):
            continue  # each pair once; opposite signs are a real transfer
        label = f"bank txns {txn.id} and {other.id}"
        if txn.journal_entry_id is None or txn.journal_entry_id != other.journal_entry_id:
            step.warnings.append(f"Skipped {label}: they do not share one transfer JE.")
            continue
        step.ops.append(DeleteEntry(txn.journal_entry, f"Transfer JE of {label}, which are both {'deposits' if txn.amount > 0 else 'withdrawals'}."))
        for t in (txn, other):
            step.ops.append(
                RestoreImportEntry(t, owner_equity, f"Owner Equity is the importer's default offset; match it again by hand.")
            )
            candidates = BankTransaction.objects.filter(
                amount=-t.amount,
                payment__isnull=True, expense__isnull=True, transfer_pair__isnull=True,
            ).exclude(bank_account=t.bank_account).select_related("bank_account").order_by("date")
            for candidate in candidates:
                step.notes.append(
                    f"Bank txn {t.id} ({t.bank_account.institution}, {t.date}, {t.amount}): a likely real "
                    f"counterpart is txn {candidate.id} ({candidate.bank_account.institution}, {candidate.date}, "
                    f"{candidate.amount}, {candidate.description!r})."
                )
            if not candidates:
                step.notes.append(
                    f"Bank txn {t.id} ({t.bank_account.institution}, {t.date}, {t.amount}, "
                    f"{t.description!r}): no unmatched {-t.amount} in another account, so probably "
                    f"not a transfer -- a charge is linked to an expense instead."
                )
    return step


def build_plan():
    return [
        plan_payment_deposits(),
        plan_receivables(),
        plan_credits_linked_to_expenses(),
        plan_same_sign_transfers(),
    ]


# --- balances ---------------------------------------------------------------------

def gl_balances():
    """{account id: debits - credits} from the database."""
    rows = JournalLine.objects.values("account").annotate(d=Sum("debit"), c=Sum("credit"))
    return defaultdict(
        lambda: ZERO,
        {r["account"]: ((r["d"] or ZERO) - (r["c"] or ZERO)).quantize(CENT) for r in rows},
    )


def simulate(balances, steps):
    after = defaultdict(lambda: ZERO, balances)
    for step in steps:
        for op in step.ops:
            for account, debit, credit in op.deltas():
                after[account.pk] += debit - credit
    return after


def register_vs_gl(balances):
    """[(bank account, register balance, GL balance)]. On this system a bank
    register equals its GL account's debits minus credits: deposits and
    payments are positive, withdrawals and charges negative."""
    return [
        (b, b.balance.quantize(CENT), balances[b.account_id])
        for b in BankAccount.objects.select_related("account").order_by("id")
    ]


class Command(BaseCommand):
    help = "Repair ledger postings from the 3.3.4 bugs. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Make the changes (default: dry run).")
        parser.add_argument("--dry-run", action="store_true", help="Explicit dry run (the default).")

    def handle(self, *args, **options):
        apply = options["apply"]
        if apply and options["dry_run"]:
            raise CommandError("Choose --apply or --dry-run, not both.")

        with transaction.atomic():
            if not apply and connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
            try:
                self._run(apply)
            finally:
                if not apply:
                    transaction.set_rollback(True)

    def _run(self, apply):
        out = self.stdout.write
        before = gl_balances()
        steps = build_plan()
        after = simulate(before, steps)
        n_ops = sum(len(s.ops) for s in steps)

        out(("APPLYING" if apply else "DRY RUN: nothing will be changed") + f". {n_ops} change(s) found.")
        out("")
        for step in steps:
            out(self.style.MIGRATE_HEADING(f"{step.title}: {len(step.ops)} change(s)"))
            for op in step.ops:
                out(f"  - {op.describe()}")
            for warning in step.warnings:
                out(self.style.WARNING(f"  ! {warning}"))
            for note in step.notes:
                out(f"  > {note}")
            out("")

        self._trial_balance(before, after)
        problems = self._checks(before, after)

        if not apply:
            out("Dry run: nothing was changed. Back up the database, then re-run with --apply.")
            return
        if problems:
            raise CommandError("Refusing to apply: the repaired ledger would still fail the checks above.")

        for step in steps:
            for op in step.ops:
                op.apply()

        actual = gl_balances()
        mismatched = [
            pk for pk in set(actual) | set(after) if actual[pk] != after[pk]
        ]
        if mismatched:
            names = ", ".join(str(a) for a in ChartOfAccount.objects.filter(pk__in=mismatched))
            raise CommandError(
                f"Rolled back: balances after applying differ from the dry run's prediction ({names})."
            )
        out(self.style.SUCCESS(f"Applied {n_ops} change(s); balances match the prediction."))

    def _trial_balance(self, before, after):
        out = self.stdout.write
        out(self.style.MIGRATE_HEADING("Trial balance (debits - credits)"))
        out(f"  {'account':<48} {'before':>12} {'after':>12} {'change':>12}")
        accounts = ChartOfAccount.objects.filter(pk__in=set(before) | set(after)).order_by("code")
        for acct in accounts:
            b, a = before[acct.pk], after[acct.pk]
            if b == 0 and a == 0:
                continue
            mark = "*" if a != b else " "
            out(f" {mark}{str(acct)[:48]:<48} {b:>12} {a:>12} {a - b:>+12}")
        out(f"  {'total (must be 0)':<48} {sum(before.values()):>12} {sum(after.values()):>12}")
        out("")

    def _checks(self, before, after):
        out = self.stdout.write
        problems = 0

        def check(ok, text):
            nonlocal problems
            problems += not ok
            out(f"  {'OK  ' if ok else 'FAIL'} {text}")

        out(self.style.MIGRATE_HEADING("Bank registers vs GL (the check is on 'after')"))
        for bank, register, gl_after in register_vs_gl(after):
            gl_before = before[bank.account_id]
            check(
                register == gl_after,
                f"{bank}: register {register}; GL {bank.account.code} before {gl_before} "
                f"(gap {register - gl_before}), after {gl_after} (gap {register - gl_after})",
            )
        out("")

        out(self.style.MIGRATE_HEADING("Ledger checks, after"))
        by_code = {a.code: a for a in ChartOfAccount.objects.filter(
            code__in=[gl_codes.CASH, gl_codes.ACCOUNTS_RECEIVABLE, LEGACY_PAYMENT_AR]
        )}
        # Informational: Cash legitimately holds payments not yet linked to a
        # deposit, and 1200 keeps any non-payment lines (warned about above).
        for code, meaning in (
            (gl_codes.CASH, "0 once every payment is linked to its deposit"),
            (LEGACY_PAYMENT_AR, "0: AR is on one account"),
            (gl_codes.ACCOUNTS_RECEIVABLE, "all receivables"),
        ):
            if code in by_code:
                out(f"  info {by_code[code]} = {after[by_code[code].pk]} ({meaning})")
        check(sum(after.values()) == 0, "debits equal credits")
        out("")
        return problems
