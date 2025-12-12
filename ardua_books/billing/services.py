# billing/services.py

import datetime

from .models import (
    Invoice,
    InvoiceLine,
    TimeEntry,
    Expense,
    BillableStatus,
)

from django.core.exceptions import ValidationError


def generate_next_invoice_number() -> str:
    """
    Simple invoice numbering: YYYY-XXX (001, 002, ...).

    Looks at existing invoice numbers starting with the current year and
    increments the numeric suffix. If nothing is found or the format is
    unexpected, it starts at YYYY-001.
    """
    today = datetime.date.today()
    year = today.year

    last_invoice = (
        Invoice.objects.filter(invoice_number__startswith=str(year))
        .order_by("-invoice_number")
        .first()
    )

    if not last_invoice:
        return f"{year}-001"

    try:
        # Expect something like "2025-007"
        _, seq_str = last_invoice.invoice_number.split("-", 1)
        seq = int(seq_str)
    except Exception:
        # Fallback if the format is different
        return f"{year}-001"

    return f"{year}-{seq + 1:03d}"

def attach_unbilled_items_to_invoice(invoice, time_ids, expense_ids):
    """
    Used by invoice create AND update.
    Creates new InvoiceLine objects for selected unbilled items.
    """
    # ---- TIME ENTRIES ----
    for te in TimeEntry.objects.filter(id__in=time_ids):

        line = InvoiceLine.objects.create(
            invoice=invoice,
            line_type=InvoiceLine.LineType.TIME,
            description=f"{te.work_date} {te.description}",
            quantity=te.hours,
            unit_price=te.billing_rate,
        )

        # Attach FK
        te.invoice_line = line
        te.status = BillableStatus.BILLED
        te.save()

        # Re-save line to compute line_total
        line.save()

    # ---- EXPENSES ----
    for ex in Expense.objects.filter(id__in=expense_ids):

        line = InvoiceLine.objects.create(
            invoice=invoice,
            line_type=InvoiceLine.LineType.EXPENSE,
            description=f"{ex.expense_date} {ex.description}",
            quantity=1,
            unit_price=ex.amount,
        )

        ex.invoice_line = line
        ex.status = BillableStatus.BILLED
        ex.save()

        line.save()


def detach_invoice_lines(invoice, lines_to_detach):
    """
    Used by invoice_update.
    lines_to_detach: list of InvoiceLine IDs
    Correct order: unset FK first, then delete line.
    """
    for line in InvoiceLine.objects.filter(id__in=lines_to_detach):

        # TIME ENTRY?
        if hasattr(line, "time_entry") and line.time_entry:
            te = line.time_entry
            te.invoice_line = None
            te.status = BillableStatus.UNBILLED
            te.save()

        # EXPENSE?
        if hasattr(line, "expense") and line.expense:
            ex = line.expense
            ex.invoice_line = None
            ex.status = BillableStatus.UNBILLED
            ex.save()

        # Now safe to delete the line
        line.delete()

def mark_all_te_ex_unbilled_and_unlink(invoice):
    """
    Used when VOIDING an invoice (DRAFT or ISSUED).
    - TimeEntry / Expense → UNBILLED
    - invoice_line FK → NULL
    - InvoiceLine rows are PRESERVED (historical)
    """
    for line in invoice.lines.all():

        if line.line_type == InvoiceLine.LineType.TIME:
            te = getattr(line, "time_entry", None)
            if te:
                te.status = BillableStatus.UNBILLED
                te.invoice_line = None
                te.save()

        elif line.line_type == InvoiceLine.LineType.EXPENSE:
            ex = getattr(line, "expense", None)
            if ex:
                ex.status = BillableStatus.UNBILLED
                ex.invoice_line = None
                ex.save()

def mark_te_ex_unbilled_keep_invoice_lines(invoice):
    """
    Used when returning ISSUED → DRAFT.
    - TimeEntry / Expense → UNBILLED
    - invoice_line FK is KEPT
    - InvoiceLine rows are PRESERVED
    """
    for line in invoice.lines.all():

        if line.line_type == InvoiceLine.LineType.TIME:
            te = getattr(line, "time_entry", None)
            if te and te.status != BillableStatus.UNBILLED:
                te.status = BillableStatus.UNBILLED
                # KEEP te.invoice_line
                te.save()

        elif line.line_type == InvoiceLine.LineType.EXPENSE:
            ex = getattr(line, "expense", None)
            if ex and ex.status != BillableStatus.UNBILLED:
                ex.status = BillableStatus.UNBILLED
                # KEEP ex.invoice_line
                ex.save()