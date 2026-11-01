"""
Tests for the ledger-posting bugs fixed in 3.3.4 and 3.3.5, and for the
repair_ledger_postings command that fixes data posted before the fixes.

1. Linking a deposit to a payment left the deposit's import JE in place and
   posted the payment to 1000 Cash: the money was counted twice.
2. Payments credited AR 1200 while invoices debit AR 1100.
3. A credit (refund) linked to an expense was posted as a charge.
4. A "transfer" could pair two withdrawals.
5. (3.3.5) Linking a payment re-dated the payment but not its JE.
6. (3.3.5) "Mark as Owner Equity" called a service method that did not exist.

The invariant behind most assertions: a bank account's register (opening
balance + transaction amounts) equals its GL account's debits minus credits.
"""

from datetime import date
from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from accounting import gl_codes
from accounting.models import (
    AccountType,
    BankAccountType,
    BankImportProfile,
    BankTransaction,
    ChartOfAccount,
    JournalEntry,
    JournalLine,
    Payment,
    PaymentApplication,
    PaymentMethod,
)
from accounting.services.banking import BankTransactionService
from accounting.services.posting import post_invoice
from billing.models import Expense, Invoice, InvoiceStatus
from conftest import (
    BankAccountFactory,
    ChartOfAccountFactory,
    ClientFactory,
    ExpenseCategoryFactory,
    UserFactory,
)

D = Decimal


# --- fixtures and helpers -----------------------------------------------------

@pytest.fixture
def gl(db):
    """The fixed accounts posting code looks up by code."""
    accounts = {}
    for code, name, kind in [
        (gl_codes.CASH, "Cash", AccountType.ASSET),
        (gl_codes.ACCOUNTS_RECEIVABLE, "Accounts Receivable", AccountType.ASSET),
        ("1200", "Accounts Receivable", AccountType.ASSET),  # the legacy second AR
        (gl_codes.UNAPPLIED_PAYMENTS, "Unapplied Payments", AccountType.LIABILITY),
        (gl_codes.OWNER_EQUITY, "Owner Equity", AccountType.EQUITY),
        (gl_codes.REVENUE, "Consulting Revenue", AccountType.INCOME),
    ]:
        accounts[code], _ = ChartOfAccount.objects.get_or_create(
            code=code, defaults={"name": name, "type": kind}
        )
    return accounts


def make_account(kind, sign_rule=None, date_idx=0, desc_idx=1, amount_idx=2):
    """A bank or card account with a CSV import profile (date, description, amount)."""
    is_card = kind == BankAccountType.CREDIT_CARD
    account = BankAccountFactory(
        type=kind,
        account=ChartOfAccountFactory(type=AccountType.LIABILITY if is_card else AccountType.ASSET),
    )
    BankImportProfile.objects.create(
        bank_account=account,
        date_column_index=date_idx,
        description_column_index=desc_idx,
        amount_column_index=amount_idx,
        date_format="%m/%d/%Y",
        sign_rule=sign_rule or ("CC_CHARGES_NEGATIVE" if is_card else "BANK_STANDARD"),
    )
    return account


@pytest.fixture
def checking(gl):
    return make_account(BankAccountType.CHECKING)


@pytest.fixture
def card(gl):
    # Chase-style: charges negative, payments and credits positive.
    return make_account(BankAccountType.CREDIT_CARD)


@pytest.fixture
def other_card(gl):
    return make_account(BankAccountType.CREDIT_CARD)


@pytest.fixture
def travel(db):
    """An expense category with its GL account."""
    return ExpenseCategoryFactory(name="Travel", account=ChartOfAccountFactory(type=AccountType.EXPENSE))


def deposit(account, amount, description="DEPOSIT", when=date(2026, 1, 13)):
    """A transaction as the importer posts it: offset Owner Equity."""
    return BankTransactionService.post_transaction(
        bank_account=account,
        date=when,
        description=description,
        amount=D(amount),
        offset_account=ChartOfAccount.objects.get(code=gl_codes.OWNER_EQUITY),
    )


def balance(account):
    """GL balance: debits - credits."""
    agg = JournalLine.objects.filter(account=account).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or D("0")) - (agg["c"] or D("0"))


def code_balance(code):
    return balance(ChartOfAccount.objects.get(code=code))


def register_equals_gl(bank_account):
    bank_account.refresh_from_db()
    return bank_account.balance == balance(bank_account.account)


def txn_entries(txn):
    """Every JE sourced from this bank transaction."""
    return JournalEntry.objects.filter(
        source_content_type=ContentType.objects.get_for_model(BankTransaction),
        source_object_id=txn.id,
    )


def entry_date(je):
    je.refresh_from_db()
    return timezone.localdate(je.posted_at)


def lines(je):
    return sorted(
        (l.account.code, l.debit, l.credit) for l in je.lines.select_related("account")
    )


def recorded_payment(client, amount, invoice=None):
    """A payment entered and posted the way the payment views do, before any
    bank transaction is linked: it debits Cash."""
    payment = Payment.objects.create(
        client=client, date=date(2026, 1, 10), amount=D(amount),
        method=PaymentMethod.ACH, unapplied_amount=D(amount),
    )
    if invoice is not None:
        PaymentApplication.objects.create(payment=payment, invoice=invoice, amount=D(amount))
        payment.unapplied_amount = D("0")
        payment.save()
    payment.post_to_accounting()
    return payment


def issued_invoice(client, total):
    invoice = Invoice.objects.create(
        client=client, invoice_number=f"INV-{Invoice.objects.count() + 1}",
        issue_date=date(2026, 1, 2), due_date=date(2026, 2, 1),
        status=InvoiceStatus.ISSUED, subtotal=D(total), total=D(total),
    )
    post_invoice(invoice)
    return invoice


@pytest.fixture
def logged_in(client, db):
    client.force_login(UserFactory())
    return client


def import_csv(client, account, rows):
    """Import rows of (mm/dd/yyyy, description, amount) through the import view."""
    content = "Date,Description,Amount\n" + "".join(f"{d},{desc},{amt}\n" for d, desc, amt in rows)
    response = client.post(
        reverse("accounting:banktransaction_import", args=[account.pk]),
        {
            "file": SimpleUploadedFile("activity.csv", content.encode(), content_type="text/csv"),
            "offset_account": ChartOfAccount.objects.get(code=gl_codes.OWNER_EQUITY).pk,
        },
        follow=True,
    )
    assert response.status_code == 200
    return list(BankTransaction.objects.filter(bank_account=account).order_by("date", "id"))


def message_texts(response):
    return [str(m) for m in response.context["messages"]]


# --- bug 1: payments linked to deposits ---------------------------------------

class TestPaymentLinkedToDeposit:
    def test_link_moves_payment_to_the_deposit_account_and_drops_the_import_entry(self, checking):
        txn = deposit(checking, "1397.41")
        payment = recorded_payment(ClientFactory(), "1397.41")
        assert code_balance(gl_codes.CASH) == D("1397.41")  # undeposited until linked

        BankTransactionService.link_existing_payment(txn, payment)

        txn.refresh_from_db()
        assert txn.journal_entry.source_object == payment
        assert lines(txn.journal_entry) == sorted([
            (checking.account.code, D("1397.41"), D("0")),
            (gl_codes.UNAPPLIED_PAYMENTS, D("0"), D("1397.41")),
        ])
        assert not txn_entries(txn).exists()  # the import JE is gone
        assert code_balance(gl_codes.CASH) == 0
        assert code_balance(gl_codes.OWNER_EQUITY) == 0
        assert register_equals_gl(checking)

    def test_unposted_payment_is_posted_straight_to_the_deposit_account(self, checking):
        txn = deposit(checking, "700.00")
        payment = Payment.objects.create(
            client=ClientFactory(), date=date(2026, 1, 20), amount=D("700.00"),
            method=PaymentMethod.CHECK, unapplied_amount=D("700.00"),
        )

        BankTransactionService.link_existing_payment(txn, payment)

        assert balance(checking.account) == D("700.00")
        assert code_balance(gl_codes.CASH) == 0
        assert register_equals_gl(checking)

    def test_create_payment_from_transaction_posts_to_the_deposit_account(self, checking):
        txn = deposit(checking, "250.00")

        BankTransactionService.create_payment_from_transaction(
            txn, ClientFactory(), date(2026, 1, 13), "250.00", PaymentMethod.ACH, "memo"
        )

        assert code_balance(gl_codes.CASH) == 0
        assert code_balance(gl_codes.OWNER_EQUITY) == 0
        assert register_equals_gl(checking)

    def test_payment_without_a_deposit_keeps_debiting_cash(self, gl):
        recorded_payment(ClientFactory(), "300.00")
        assert code_balance(gl_codes.CASH) == D("300.00")

    def test_payment_already_linked_to_another_deposit_is_refused(self, checking):
        first, second = deposit(checking, "500.00"), deposit(checking, "500.00")
        payment = recorded_payment(ClientFactory(), "500.00")
        BankTransactionService.link_existing_payment(first, payment)

        with pytest.raises(ValueError, match="already linked to another bank transaction"):
            BankTransactionService.link_existing_payment(second, payment)
        assert register_equals_gl(checking)

    def test_transaction_matched_to_an_expense_cannot_take_a_payment(self, card, travel):
        txn = deposit(card, "40.00", "REFUND")
        BankTransactionService.link_expense(
            txn, Expense.objects.create(category=travel, amount=D("-40.00"), billable=False)
        )
        payment = recorded_payment(ClientFactory(), "40.00")

        with pytest.raises(ValueError, match="already matched"):
            BankTransactionService.link_existing_payment(txn, payment)


# --- bug 2: one accounts receivable -------------------------------------------

class TestReceivables:
    def test_invoice_and_payment_use_the_same_receivable_account(self, checking):
        client = ClientFactory()
        invoice = issued_invoice(client, "1105.00")
        payment = recorded_payment(client, "1105.00", invoice=invoice)
        BankTransactionService.link_existing_payment(deposit(checking, "1105.00"), payment)

        assert code_balance(gl_codes.ACCOUNTS_RECEIVABLE) == 0  # invoiced, then paid
        assert code_balance("1200") == 0
        assert register_equals_gl(checking)


# --- bug 3: credits linked to expenses ----------------------------------------

class TestCreditsLinkedToExpenses:
    def test_refund_is_posted_as_a_contra_expense(self, card, travel):
        txn = deposit(card, "200.00", "AMEX DELTA STAYS CREDIT")
        refund = Expense.objects.create(category=travel, amount=D("-200.00"), billable=False)

        BankTransactionService.link_expense(txn, refund)

        txn.refresh_from_db()
        assert lines(txn.journal_entry) == sorted([
            (card.account.code, D("200.00"), D("0")),
            (travel.account.code, D("0"), D("200.00")),
        ])
        assert balance(travel.account) == D("-200.00")  # reduces the expense
        assert register_equals_gl(card)

    def test_refund_cannot_be_linked_to_a_positive_expense(self, card, travel):
        txn = deposit(card, "20.06", "AMAZON MKTPLACE PMTS")
        import_entry = txn.journal_entry

        with pytest.raises(ValueError, match="is a credit"):
            BankTransactionService.link_expense(
                txn, Expense.objects.create(category=travel, amount=D("20.06"), billable=False)
            )
        assert JournalEntry.objects.filter(pk=import_entry.pk).exists()
        assert register_equals_gl(card)

    def test_charge_cannot_be_linked_to_a_refund(self, card, travel):
        txn = deposit(card, "-20.06", "AMAZON")
        with pytest.raises(ValueError, match="is a charge"):
            BankTransactionService.link_expense(
                txn, Expense.objects.create(category=travel, amount=D("-20.06"), billable=False)
            )

    def test_charge_is_still_posted_as_an_expense(self, card, travel):
        txn = deposit(card, "-158.08", "TRAINLINE")

        BankTransactionService.link_expense(
            txn, Expense.objects.create(category=travel, amount=D("158.08"), billable=False)
        )

        assert balance(travel.account) == D("158.08")
        assert register_equals_gl(card)


# --- bug 4: transfers ----------------------------------------------------------

class TestTransfers:
    def test_two_withdrawals_are_not_a_transfer(self, checking, card):
        epay = deposit(checking, "-1.64", "CHASE CREDIT CRD EPAY")
        costco = deposit(card, "-1.64", "COSTCO WHSE #0113")

        with pytest.raises(ValueError, match="both transactions are withdrawals"):
            BankTransactionService.match_transfer(epay, costco)

        epay.refresh_from_db(); costco.refresh_from_db()
        assert epay.transfer_pair is None and costco.transfer_pair is None
        assert register_equals_gl(checking) and register_equals_gl(card)

    def test_two_deposits_are_not_a_transfer(self, checking, card):
        with pytest.raises(ValueError, match="both transactions are deposits"):
            BankTransactionService.match_transfer(deposit(checking, "5.00"), deposit(card, "5.00"))

    def test_card_payment_from_checking_is_a_transfer(self, checking, card):
        epay = deposit(checking, "-1.64", "CHASE CREDIT CRD EPAY")
        payment = deposit(card, "1.64", "Payment Thank You-Mobile")

        BankTransactionService.match_transfer(epay, payment)

        epay.refresh_from_db()
        assert lines(epay.journal_entry) == sorted([
            (card.account.code, D("1.64"), D("0")),
            (checking.account.code, D("0"), D("1.64")),
        ])
        assert register_equals_gl(checking) and register_equals_gl(card)
        assert code_balance(gl_codes.OWNER_EQUITY) == 0


# --- 3.3.5: payment entry dates --------------------------------------------------

class TestPaymentEntryDate:
    def test_payment_recorded_before_its_deposit_takes_the_deposit_date(self, checking):
        txn = deposit(checking, "1397.41", when=date(2026, 1, 13))
        payment = recorded_payment(ClientFactory(), "1397.41")  # dated 2026-01-10, posted then
        payment_je = JournalEntry.objects.get(
            source_content_type=ContentType.objects.get_for_model(Payment), source_object_id=payment.id
        )
        assert entry_date(payment_je) == date(2026, 1, 10)

        BankTransactionService.link_existing_payment(txn, payment)

        payment.refresh_from_db()
        assert payment.date == date(2026, 1, 13)
        assert entry_date(payment_je) == date(2026, 1, 13)

    def test_unposted_payment_is_posted_on_the_deposit_date(self, checking):
        txn = deposit(checking, "700.00", when=date(2026, 1, 26))
        payment = Payment.objects.create(
            client=ClientFactory(), date=date(2026, 1, 20), amount=D("700.00"),
            method=PaymentMethod.CHECK, unapplied_amount=D("700.00"),
        )

        BankTransactionService.link_existing_payment(txn, payment)

        txn.refresh_from_db()
        assert entry_date(txn.journal_entry) == date(2026, 1, 26)


# --- 3.3.5: Mark as Owner Equity --------------------------------------------------

def posted_against(account, amount, offset_account, when=date(2026, 2, 3)):
    """An unmatched transaction posted against a chosen offset."""
    return BankTransactionService.post_transaction(
        bank_account=account, date=when, description="UNCATEGORIZED",
        amount=D(amount), offset_account=offset_account,
    )


class TestMarkAsOwnerEquity:
    def test_deposit_is_reposted_as_a_contribution(self, checking, gl):
        txn = posted_against(checking, "500.00", gl[gl_codes.REVENUE])
        je_id = txn.journal_entry_id

        BankTransactionService.mark_as_owner_equity(txn)

        txn.refresh_from_db()
        assert txn.offset_account.code == gl_codes.OWNER_EQUITY
        assert txn.journal_entry_id == je_id  # rebuilt in place, not duplicated
        assert lines(txn.journal_entry) == sorted([
            (checking.account.code, D("500.00"), D("0")),
            (gl_codes.OWNER_EQUITY, D("0"), D("500.00")),
        ])
        assert code_balance(gl_codes.REVENUE) == 0
        assert register_equals_gl(checking)

    def test_charge_is_reposted_as_a_draw(self, card, travel):
        txn = posted_against(card, "-42.50", travel.account)

        BankTransactionService.mark_as_owner_equity(txn)

        txn.refresh_from_db()
        assert lines(txn.journal_entry) == sorted([
            (gl_codes.OWNER_EQUITY, D("42.50"), D("0")),
            (card.account.code, D("0"), D("42.50")),
        ])
        assert balance(travel.account) == 0
        assert register_equals_gl(card)

    def test_transaction_without_an_entry_gets_one(self, checking, gl):
        txn = posted_against(checking, "75.00", gl[gl_codes.REVENUE])
        txn.journal_entry.delete()
        txn.refresh_from_db()
        assert not register_equals_gl(checking)

        BankTransactionService.mark_as_owner_equity(txn)

        txn.refresh_from_db()
        assert txn.journal_entry.source_object == txn
        assert entry_date(txn.journal_entry) == txn.date
        assert register_equals_gl(checking)

    def test_matched_transaction_is_refused_and_left_alone(self, card, travel):
        txn = deposit(card, "-60.00", "DELTA AIR")
        expense = Expense.objects.create(category=travel, amount=D("60.00"), billable=False)
        BankTransactionService.link_expense(txn, expense)
        txn.refresh_from_db()
        before = lines(txn.journal_entry)

        with pytest.raises(ValueError, match="only an unmatched transaction"):
            BankTransactionService.mark_as_owner_equity(txn)

        txn.refresh_from_db()
        assert lines(txn.journal_entry) == before
        assert txn.expense_id == expense.id

    def test_entry_belonging_to_something_else_is_not_rebuilt(self, checking, gl):
        txn = posted_against(checking, "90.00", gl[gl_codes.REVENUE])
        other = posted_against(checking, "15.00", gl[gl_codes.REVENUE])
        BankTransaction.objects.filter(pk=txn.pk).update(journal_entry=other.journal_entry)
        txn.refresh_from_db()
        before = lines(other.journal_entry)

        with pytest.raises(ValueError, match="does not belong to bank transaction"):
            BankTransactionService.mark_as_owner_equity(txn)
        assert lines(other.journal_entry) == before

    def test_mark_through_the_view(self, logged_in, checking, gl):
        txn = posted_against(checking, "-250.00", gl[gl_codes.REVENUE])
        url = reverse("accounting:banktransaction_mark_owner_equity", args=[txn.pk])

        assert logged_in.get(url).status_code == 200
        response = logged_in.post(url, follow=True)

        assert "Transaction recorded as Owner Equity." in message_texts(response)
        txn.refresh_from_db()
        assert txn.offset_account.code == gl_codes.OWNER_EQUITY
        assert code_balance(gl_codes.OWNER_EQUITY) == D("250.00")  # a draw
        assert register_equals_gl(checking)

    def test_view_reports_a_matched_transaction(self, logged_in, checking, card):
        epay = deposit(checking, "-80.00", "CARD EPAY")
        card_payment = deposit(card, "80.00", "PAYMENT THANK YOU")
        BankTransactionService.match_transfer(epay, card_payment)

        response = logged_in.post(
            reverse("accounting:banktransaction_mark_owner_equity", args=[epay.pk]), follow=True
        )

        assert any(m.startswith("Could not mark as equity: This transaction is matched")
                   for m in message_texts(response))
        epay.refresh_from_db()
        assert epay.transfer_pair_id == card_payment.id
        assert register_equals_gl(checking) and register_equals_gl(card)


# --- end to end: import through the view, then link through the view ------------

class TestEndToEnd:
    def test_import_deposit_then_create_payment_from_it(self, logged_in, checking):
        [txn] = import_csv(logged_in, checking, [("01/13/2026", "ACH CREDIT CLIENT", "1759.68")])
        client = ClientFactory()

        response = logged_in.post(
            reverse("accounting:payment_create_from_transaction", args=[txn.pk]),
            {"client": client.pk, "date": "2026-01-13", "amount": "1759.68",
             "method": PaymentMethod.ACH, "memo": "ACH CREDIT CLIENT"},
            follow=True,
        )

        assert "Payment created and linked to transaction." in message_texts(response)
        assert code_balance(gl_codes.CASH) == 0
        assert code_balance(gl_codes.OWNER_EQUITY) == 0
        assert register_equals_gl(checking)

    def test_payment_entered_with_an_earlier_date_is_posted_on_the_deposit_date(self, logged_in, checking):
        [txn] = import_csv(logged_in, checking, [("04/09/2025", "MOBILE DEPOSIT", "4875.00")])

        logged_in.post(
            reverse("accounting:payment_create_from_transaction", args=[txn.pk]),
            {"client": ClientFactory().pk, "date": "2025-03-31", "amount": "4875.00",
             "method": PaymentMethod.CHECK, "memo": "check dated 3/31"},
            follow=True,
        )

        txn.refresh_from_db()
        assert txn.payment.date == date(2025, 4, 9)
        assert entry_date(txn.journal_entry) == date(2025, 4, 9)

    def test_import_deposit_then_link_existing_payment(self, logged_in, checking):
        [txn] = import_csv(logged_in, checking, [("01/26/2026", "MOBILE DEPOSIT", "700.00")])
        client = ClientFactory()
        invoice = issued_invoice(client, "700.00")
        payment = recorded_payment(client, "700.00", invoice=invoice)

        response = logged_in.post(
            reverse("accounting:banktransaction_link_payment", args=[txn.pk]),
            {"payment": payment.pk},
            follow=True,
        )

        assert "Transaction linked to payment." in message_texts(response)
        assert code_balance(gl_codes.CASH) == 0
        assert code_balance(gl_codes.ACCOUNTS_RECEIVABLE) == 0
        assert code_balance(gl_codes.OWNER_EQUITY) == 0
        assert register_equals_gl(checking)

    def test_import_refund_then_create_expense_from_it(self, logged_in, card, travel):
        [txn] = import_csv(logged_in, card, [("01/09/2026", "AMEX DELTA STAYS CREDIT", "200.00")])

        logged_in.post(
            reverse("accounting:banktransaction_link_expense", args=[txn.pk]),
            {"create_new": "on", "category": travel.pk},
            follow=True,
        )

        txn.refresh_from_db()
        assert txn.expense.amount == D("-200.00")  # a refund, not a positive expense
        assert balance(travel.account) == D("-200.00")
        assert register_equals_gl(card)

    def test_import_both_sides_then_match_the_transfer(self, logged_in, checking, card):
        [epay] = import_csv(logged_in, checking, [("07/21/2025", "CHASE CREDIT CRD EPAY", "-1.64")])
        costco, card_payment = import_csv(logged_in, card, [
            ("05/13/2025", "COSTCO WHSE #0113", "-1.64"),
            ("07/20/2025", "Payment Thank You-Mobile", "1.64"),
        ])
        url = reverse("accounting:banktransaction_match_transfer", args=[epay.pk])

        logged_in.post(url, {"target_transaction": costco.pk}, follow=True)
        costco.refresh_from_db()
        assert costco.transfer_pair is None  # a charge is not offered or accepted

        logged_in.post(url, {"target_transaction": card_payment.pk}, follow=True)
        card_payment.refresh_from_db()
        assert card_payment.transfer_pair_id == epay.pk
        assert register_equals_gl(checking) and register_equals_gl(card)


# --- repair_ledger_postings ------------------------------------------------------

def run_repair(*args):
    out = StringIO()
    call_command("repair_ledger_postings", *args, stdout=out)
    return out.getvalue()


def snapshot():
    """Every journal line and the fields the repair may change."""
    return (
        sorted(JournalLine.objects.values_list("id", "entry_id", "account_id", "debit", "credit")),
        sorted(JournalEntry.objects.values_list("id", "posted_at")),
        sorted(BankTransaction.objects.values_list("id", "journal_entry_id", "transfer_pair_id", "offset_account_id")),
        sorted(Expense.objects.values_list("id", "amount")),
        sorted(ChartOfAccount.objects.values_list("id", "is_active")),
    )


@pytest.fixture
def broken_ledger(checking, card, other_card, travel, gl):
    """Data in the shapes the pre-3.3.4 code posted, mirroring production."""
    ar_legacy = gl["1200"]
    cash = gl[gl_codes.CASH]
    equity = gl[gl_codes.OWNER_EQUITY]
    client = ClientFactory()
    payment_ct = ContentType.objects.get_for_model(Payment)

    # Bug 1 + 2: deposit keeps its import JE; payment JE is DR Cash / CR 1200.
    # 3.3.5: the payment JE is dated before the deposit, as payments 1-6 were.
    invoice = issued_invoice(client, "4000.00")
    txn = deposit(checking, "4000.00", "MOBILE DEPOSIT")
    payment = Payment.objects.create(
        client=client, date=txn.date, amount=D("4000.00"),
        method=PaymentMethod.CHECK, unapplied_amount=D("0"),
    )
    PaymentApplication.objects.create(payment=payment, invoice=invoice, amount=D("4000.00"))
    je = JournalEntry.objects.create(
        posted_at=date(2025, 12, 31),
        description="Payment received", source_content_type=payment_ct, source_object_id=payment.id,
    )
    JournalLine.objects.create(entry=je, account=cash, debit=D("4000.00"), credit=0)
    JournalLine.objects.create(entry=je, account=ar_legacy, debit=0, credit=D("4000.00"))
    BankTransaction.objects.filter(pk=txn.pk).update(payment=payment, journal_entry=je)

    # Bug 3: a refund linked to a positive expense, posted as a charge.
    refund_txn = deposit(card, "20.06", "AMAZON MKTPLACE PMTS")
    refund_txn.journal_entry.delete()
    expense = Expense.objects.create(category=travel, amount=D("20.06"), billable=False)
    je = JournalEntry.objects.create(
        description="Expense", source_content_type=ContentType.objects.get_for_model(Expense),
        source_object_id=expense.id,
    )
    JournalLine.objects.create(entry=je, account=travel.account, debit=D("20.06"), credit=0)
    JournalLine.objects.create(entry=je, account=card.account, debit=0, credit=D("20.06"))
    BankTransaction.objects.filter(pk=refund_txn.pk).update(expense=expense, journal_entry=je)

    # Bug 4: two withdrawals paired as a transfer. As in production, the match
    # was started from the card charge, so the old match_transfer treated it
    # as the source: DR checking / CR card -- backwards for the checking side.
    epay = deposit(checking, "-1.64", "CHASE CREDIT CRD EPAY", date(2025, 7, 21))
    costco = deposit(other_card, "-1.64", "COSTCO WHSE #0113", date(2025, 5, 13))
    real_payment = deposit(other_card, "1.64", "Payment Thank You-Mobile", date(2025, 7, 20))
    epay.journal_entry.delete()
    costco.journal_entry.delete()
    je = JournalEntry.objects.create(
        description="Transfer", source_content_type=ContentType.objects.get_for_model(BankTransaction),
        source_object_id=costco.id,
    )
    JournalLine.objects.create(entry=je, account=checking.account, debit=D("1.64"), credit=0)
    JournalLine.objects.create(entry=je, account=other_card.account, debit=0, credit=D("1.64"))
    BankTransaction.objects.filter(pk=epay.pk).update(journal_entry=je, transfer_pair=costco, offset_account=other_card.account)
    BankTransaction.objects.filter(pk=costco.pk).update(journal_entry=je, transfer_pair=epay, offset_account=checking.account)

    assert not register_equals_gl(checking)
    assert not register_equals_gl(card)
    return dict(txn=txn, payment=payment, refund_txn=refund_txn, expense=expense,
                epay=epay, costco=costco, real_payment=real_payment, equity=equity)


class TestRepairCommand:
    def test_dry_run_reports_every_change_and_writes_nothing(self, broken_ledger):
        before = snapshot()

        output = run_repair()

        assert snapshot() == before
        assert "DRY RUN" in output and "Dry run: nothing was changed" in output
        assert "1. Payments linked to bank deposits: 2 change(s)" in output
        assert "2. Accounts receivable: 1200 -> 1100: 2 change(s)" in output  # move + deactivate
        assert "3. Credits (refunds) linked to expenses: 2 change(s)" in output
        assert "4. Transfers that pair two withdrawals or two deposits: 3 change(s)" in output
        assert "5. Payment entries dated differently from their deposit: 1 change(s)" in output
        assert "posted_at 2025-12-31 -> 2026-01-13" in output
        assert "FAIL" not in output  # the predicted ledger reconciles
        assert f"a likely real counterpart is txn {broken_ledger['real_payment'].id}" in output

    def test_explicit_dry_run_flag(self, broken_ledger):
        before = snapshot()
        assert "DRY RUN" in run_repair("--dry-run")
        assert snapshot() == before

    def test_apply_repairs_the_ledger(self, broken_ledger, checking, card, other_card):
        b = broken_ledger

        output = run_repair("--apply")

        assert "balances match the prediction" in output
        for account in (checking, card, other_card):
            assert register_equals_gl(account), account
        assert code_balance(gl_codes.CASH) == 0
        assert code_balance(gl_codes.ACCOUNTS_RECEIVABLE) == 0
        assert code_balance("1200") == 0
        assert not ChartOfAccount.objects.get(code="1200").is_active

        b["txn"].refresh_from_db()
        assert not txn_entries(b["txn"]).exists()
        assert b["txn"].journal_entry.source_object == b["payment"]
        assert entry_date(b["txn"].journal_entry) == b["txn"].date

        b["expense"].refresh_from_db()
        assert b["expense"].amount == D("-20.06")

        for t in (b["epay"], b["costco"]):
            t.refresh_from_db()
            assert t.transfer_pair is None
            assert t.offset_account == b["equity"]
            assert t.journal_entry.source_object == t

    def test_second_run_finds_nothing(self, broken_ledger):
        run_repair("--apply")
        before = snapshot()

        output = run_repair("--apply")

        assert "0 change(s) found" in output
        assert snapshot() == before

    def test_payment_whose_amount_differs_from_its_deposit_is_skipped(self, broken_ledger):
        Payment.objects.filter(pk=broken_ledger["payment"].pk).update(amount=D("3999.00"))

        output = run_repair()

        assert "differs from the deposit's 4000.00" in output
        assert "1. Payments linked to bank deposits: 0 change(s)" in output
        assert "5. Payment entries dated differently from their deposit: 0 change(s)" in output

    def test_non_payment_line_on_1200_is_left_alone_and_1200_stays_active(self, broken_ledger, gl):
        je = JournalEntry.objects.create(description="Manual AR adjustment")
        JournalLine.objects.create(entry=je, account=gl["1200"], debit=D("5.00"), credit=0)
        JournalLine.objects.create(entry=je, account=gl[gl_codes.REVENUE], debit=0, credit=D("5.00"))

        run_repair("--apply")

        assert ChartOfAccount.objects.get(code="1200").is_active
        assert code_balance("1200") == D("5.00")

    def test_apply_is_refused_when_a_register_would_still_not_reconcile(self, broken_ledger, checking):
        # A bank line no bug explains: the repair cannot reconcile it, so it must not run.
        je = JournalEntry.objects.create(description="Stray")
        JournalLine.objects.create(entry=je, account=checking.account, debit=D("9.99"), credit=0)
        JournalLine.objects.create(entry=je, account=broken_ledger["equity"], debit=0, credit=D("9.99"))
        before = snapshot()

        with pytest.raises(CommandError, match="Refusing to apply"):
            run_repair("--apply")
        assert snapshot() == before
