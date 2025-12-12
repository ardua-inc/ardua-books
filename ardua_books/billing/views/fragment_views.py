"""
AJAX fragment views for dynamic UI updates.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from billing.models import TimeEntry, Expense, BillableStatus


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

    total_time_value = sum(
        (te.hours * te.billing_rate) for te in unbilled_time
    )
    total_expense_value = sum(
        ex.amount for ex in unbilled_expenses
    )
    subtotal = total_time_value + total_expense_value

    return render(request, "billing/invoice_unbilled_fragment.html", {
        "client": client_id,
        "unbilled_time": unbilled_time,
        "unbilled_expenses": unbilled_expenses,
        "total_time_value": total_time_value,
        "total_expense_value": total_expense_value,
        "subtotal": subtotal,
    })
