# billing/services.py

import datetime

from .models import (
    Invoice,
    InvoiceLine,
    TimeEntry,
    Expense,
    BillableStatus,
    RecurringCharge,
    RecurringChargeOccurrence,
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


def attach_recurring_charges_to_invoice(invoice, charge_period_list):
    """
    Creates InvoiceLines and RecurringChargeOccurrences for the selected
    recurring charge periods and updates each charge's last_billed_date.

    charge_period_list: list of (charge_id, period_date) tuples
    """
    # Group by charge to update last_billed_date correctly
    charges_to_update = {}

    for charge_id, period_date in charge_period_list:
        try:
            charge = RecurringCharge.objects.get(id=charge_id)
        except RecurringCharge.DoesNotExist:
            continue

        # Format period label for description
        if charge.frequency == "MONTHLY":
            period_label = period_date.strftime("%B %Y")
        elif charge.frequency == "QUARTERLY":
            quarter = (period_date.month - 1) // 3 + 1
            period_label = f"Q{quarter} {period_date.year}"
        elif charge.frequency == "ANNUALLY":
            period_label = str(period_date.year)
        else:
            period_label = period_date.strftime("%B %Y")

        line = InvoiceLine.objects.create(
            invoice=invoice,
            line_type=InvoiceLine.LineType.RECURRING,
            description=f"{charge.description} ({period_label})",
            quantity=1,
            unit_price=charge.amount,
        )

        RecurringChargeOccurrence.objects.create(
            recurring_charge=charge,
            invoice_line=line,
            amount_billed=charge.amount,
            occurrence_date=period_date,
        )

        line.save()

        # Track the latest period_date for each charge
        if charge_id not in charges_to_update or period_date > charges_to_update[charge_id][1]:
            charges_to_update[charge_id] = (charge, period_date)

    # Update last_billed_date for each charge to the latest billed period
    for charge, latest_period in charges_to_update.values():
        if charge.last_billed_date is None or latest_period > charge.last_billed_date:
            charge.last_billed_date = latest_period
            charge.save(update_fields=["last_billed_date"])


def _recompute_charge_last_billed_date(charge):
    """
    After an occurrence is removed, reset last_billed_date from remaining
    occurrences so the charge correctly shows as due again.
    """
    latest = charge.occurrences.order_by("-occurrence_date").first()
    charge.last_billed_date = latest.occurrence_date if latest else None
    charge.save(update_fields=["last_billed_date"])


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

        # RECURRING CHARGE?
        occurrence = getattr(line, "recurring_charge_occurrence", None)
        if occurrence:
            charge = occurrence.recurring_charge
            occurrence.delete()
            _recompute_charge_last_billed_date(charge)

        # Now safe to delete the line
        line.delete()

def mark_all_te_ex_unbilled_and_unlink(invoice):
    """
    Used when VOIDING an invoice (DRAFT or ISSUED).
    - TimeEntry / Expense → UNBILLED
    - invoice_line FK → NULL
    - RecurringChargeOccurrence → deleted, last_billed_date recomputed
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

        elif line.line_type == InvoiceLine.LineType.RECURRING:
            occurrence = getattr(line, "recurring_charge_occurrence", None)
            if occurrence:
                charge = occurrence.recurring_charge
                occurrence.delete()
                _recompute_charge_last_billed_date(charge)

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