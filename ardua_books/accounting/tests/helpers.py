"""
Helpers shared by the ledger tests (test_ledger_postings, test_owner_funding).

The invariant most assertions rest on: a bank account's register (opening
balance + transaction amounts) equals its GL account's debits minus credits,
and every journal entry balances.
"""

from datetime import date
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from accounting import gl_codes
from accounting.models import (
    AccountType,
    BankAccount,
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
from billing.models import Invoice, InvoiceStatus
from conftest import BankAccountFactory, ChartOfAccountFactory

D = Decimal


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


def posted_against(account, amount, offset_account, when=date(2026, 2, 3)):
    """An unmatched transaction posted against a chosen offset."""
    return BankTransactionService.post_transaction(
        bank_account=account, date=when, description="UNCATEGORIZED",
        amount=D(amount), offset_account=offset_account,
    )


def assert_books_consistent():
    """Every bank register equals its GL account, and every JE balances."""
    for bank_account in BankAccount.objects.all():
        assert register_equals_gl(bank_account), f"register != GL for {bank_account}"
    for je in JournalEntry.objects.prefetch_related("lines"):
        debits = sum((l.debit for l in je.lines.all()), D("0"))
        credits = sum((l.credit for l in je.lines.all()), D("0"))
        assert debits == credits, f"JE {je.id} is unbalanced: {debits} != {credits}"
