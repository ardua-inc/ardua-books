from django.db import transaction
from django.contrib.contenttypes.models import ContentType

from billing.models import Invoice, InvoiceStatus
from accounting.models import (
    ChartOfAccount,
    JournalEntry,
    JournalLine,
)


def _get_default_accounts():
    ar = ChartOfAccount.objects.get(code="1100")
    revenue = ChartOfAccount.objects.get(code="4000")
    return ar, revenue


def _create_journal_line(entry, account, debit=0, credit=0):
    JournalLine.objects.create(
        entry=entry,
        account=account,
        debit=debit,
        credit=credit,
    )


def _invoice_currently_posted(invoice):
    ct = ContentType.objects.get_for_model(Invoice)
    count = JournalEntry.objects.filter(
        source_content_type=ct,
        source_object_id=invoice.id,
    ).count()
    return (count % 2) == 1


@transaction.atomic
def post_invoice(invoice, user=None):
    """
    Called only when invoice transitions DRAFT -> ISSUED.
    Creates a JournalEntry representing:
        Dr Accounts Receivable
            Cr Consulting Revenue
    """
    if _invoice_currently_posted(invoice):
        return

    ar, revenue = _get_default_accounts()

    ct = ContentType.objects.get_for_model(Invoice)
    entry = JournalEntry.objects.create(
        posted_by=user,
        description=f"Invoice {invoice.invoice_number} posted",
        source_content_type=ct,
        source_object_id=invoice.id,
    )

    _create_journal_line(
        entry,
        account=ar,
        debit=invoice.total,
        credit=0,
    )

    _create_journal_line(
        entry,
        account=revenue,
        debit=0,
        credit=invoice.total,
    )

    return entry


@transaction.atomic
def reverse_invoice(invoice, user=None):
    """
    Called when invoice transitions ISSUED -> VOID or ISSUED -> DRAFT.
    Creates the reversing entry:
        Dr Consulting Revenue
            Cr Accounts Receivable
    """
    if not _invoice_currently_posted(invoice):
        return  # nothing to reverse

    ar, revenue = _get_default_accounts()

    ct = ContentType.objects.get_for_model(Invoice)
    entry = JournalEntry.objects.create(
        posted_by=user,
        description=f"Invoice {invoice.invoice_number} reversed",
        source_content_type=ct,
        source_object_id=invoice.id,
    )

    _create_journal_line(
        entry,
        account=revenue,
        debit=invoice.total,
        credit=0,
    )

    _create_journal_line(
        entry,
        account=ar,
        debit=0,
        credit=invoice.total,
    )

    return entry

