"""
AJAX fragment views for dynamic UI updates.
"""
import datetime

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render

from billing.models import TimeEntry, Expense, BillableStatus, RecurringCharge


@login_required
def invoice_unbilled_fragment(request):
    """Return unbilled items fragment for invoice creation/editing."""
    client_id = request.GET.get("client")

    if not client_id:
        return render(request, "billing/invoice_unbilled_fragment.html", {
            "client": None
        })

    unbilled_time = TimeEntry.objects.filter(
        client_id=client_id,
        status=BillableStatus.UNBILLED,
        invoice_line__isnull=True,
    ).order_by("work_date")

    unbilled_expenses = Expense.objects.filter(
        client_id=client_id,
        billable=True,
        status=BillableStatus.UNBILLED,
        invoice_line__isnull=True,
    ).order_by("expense_date")

    today = datetime.date.today()
    recurring_charges = RecurringCharge.objects.filter(
        client_id=client_id,
        is_active=True,
        start_date__lte=today,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today)
    ).order_by("description")

    total_time_value = sum(
        (te.hours * te.billing_rate) for te in unbilled_time
    )
    total_expense_value = sum(
        ex.amount for ex in unbilled_expenses
    )
    total_recurring_value = sum(
        rc.amount for rc in recurring_charges
    )
    subtotal = total_time_value + total_expense_value + total_recurring_value

    return render(request, "billing/invoice_unbilled_fragment.html", {
        "client": client_id,
        "unbilled_time": unbilled_time,
        "unbilled_expenses": unbilled_expenses,
        "recurring_charges": recurring_charges,
        "total_time_value": total_time_value,
        "total_expense_value": total_expense_value,
        "total_recurring_value": total_recurring_value,
        "subtotal": subtotal,
    })
