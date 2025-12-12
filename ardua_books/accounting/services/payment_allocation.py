# accounting/services/payment_allocation.py

from decimal import Decimal
from django.forms import formset_factory
from billing.models import Invoice
from accounting.forms import PaymentAllocationForm


def build_initial_forms_for_invoices(invoices):
    """
    Given a list of Invoice objects, return the initial dicts needed
    to pre-populate the PaymentAllocationFormSet.
    """
    initial = []
    for inv in invoices:
        initial.append({
            "invoice_id": inv.id,
            "invoice_number": inv.invoice_number,
            "invoice_date": inv.issue_date,
            "original_amount": inv.total,
            "outstanding_balance": inv.outstanding_balance(),
            "amount_to_apply": Decimal("0.00"),
        })
    return initial


def build_formset(request, invoices):
    """
    Build a PaymentAllocationFormSet preloaded with invoice metadata.
    """
    PaymentAllocationFormSet = formset_factory(PaymentAllocationForm, extra=0)
    initial = build_initial_forms_for_invoices(invoices)
    return PaymentAllocationFormSet(request.POST or None, initial=initial)
