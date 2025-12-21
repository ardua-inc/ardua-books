"""
Payment management views.
"""
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.forms import formset_factory
from django.forms.utils import ErrorList
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView, DetailView, FormView

from accounting.forms import (
    PaymentForInvoiceForm,
    PaymentGeneralForm,
    PaymentAllocationFormSet,
    PaymentAllocationForm,
)
from accounting.models import (
    Payment,
    PaymentApplication,
    PaymentMethod,
    BankTransaction,
)
from accounting.services.payment_allocation import build_formset
from accounting.services.banking import BankTransactionService
from billing.models import Client, Invoice, InvoiceStatus
from accounting.views.mixins import FilterPersistenceMixin, ReadOnlyUserMixin


# Pagination settings
DEFAULT_PAGE_SIZE = 20
PAGE_SIZE_OPTIONS = [10, 20, 50, 100]


class PaymentListView(FilterPersistenceMixin, ListView):
    model = Payment
    template_name = "accounting/payment_list.html"
    context_object_name = "payments"
    paginate_by = None  # We handle pagination manually

    # Filter persistence
    filter_persistence_key = "payment_list_filters"
    filter_params = ["client", "date_preset", "date_from", "date_to", "per_page"]

    def get_queryset(self):
        qs = super().get_queryset().order_by("-date")

        # Client filter
        client_id = self.request.GET.get("client")
        if client_id:
            qs = qs.filter(client_id=client_id)

        # Date filtering
        today = date.today()
        date_preset = self.request.GET.get("date_preset", "")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

        if date_preset == "ytd":
            qs = qs.filter(date__gte=date(today.year, 1, 1))
        elif date_preset == "mtd":
            qs = qs.filter(date__gte=date(today.year, today.month, 1))
        elif date_preset == "last_year":
            qs = qs.filter(
                date__gte=date(today.year - 1, 1, 1),
                date__lt=date(today.year, 1, 1)
            )
        elif date_from or date_to:
            if date_from:
                qs = qs.filter(date__gte=date_from)
            if date_to:
                qs = qs.filter(date__lte=date_to)

        # Unapplied filter - handled in get_context_data since it requires property access
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get filter values
        client_id = self.request.GET.get("client", "")
        date_preset = self.request.GET.get("date_preset", "")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")
        show_filter = self.request.GET.get("show", "all")

        # Get queryset and apply unapplied filter
        payments = list(self.get_queryset())
        if show_filter == "unapplied":
            payments = [p for p in payments if p.unapplied_amount > 0]

        # Pagination
        page_size = self.request.GET.get("per_page", DEFAULT_PAGE_SIZE)
        try:
            page_size = int(page_size)
            if page_size not in PAGE_SIZE_OPTIONS:
                page_size = DEFAULT_PAGE_SIZE
        except (ValueError, TypeError):
            page_size = DEFAULT_PAGE_SIZE

        paginator = Paginator(payments, page_size)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        # Client list for dropdown
        clients = Client.objects.filter(is_active=True).order_by("name")

        # Filtered client for header
        filtered_client = None
        if client_id:
            filtered_client = Client.objects.filter(id=client_id).first()

        context.update({
            "payments": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "clients": clients,
            "client_filter": client_id,
            "filtered_client": filtered_client,
            "date_preset": date_preset,
            "date_from": date_from,
            "date_to": date_to,
            "show_filter": show_filter,
            "per_page": page_size,
            "page_size_options": PAGE_SIZE_OPTIONS,
        })
        return context


class PaymentDetailView(DetailView):
    model = Payment
    template_name = "accounting/payment_detail.html"
    context_object_name = "payment"


class PaymentCreateGeneralView(ReadOnlyUserMixin, View):
    template_name = "accounting/payment_general_form.html"

    def get(self, request):
        initial = {}
        client_id = request.GET.get("client")
        if client_id:
            initial["client"] = client_id
        header_form = PaymentGeneralForm(initial=initial)
        return render(request, self.template_name, {
            "header_form": header_form,
            "formset": None,
            "invoices": [],
        })

    def _build_formset_with_initial(self, request, invoices):
        """Create a formset initialized with invoice metadata."""
        initial = []
        for inv in invoices:
            initial.append({
                "invoice_id": inv.id,
                "invoice_number": inv.invoice_number,
                "invoice_date": inv.due_date,
                "original_amount": inv.total,
                "outstanding_balance": inv.outstanding_balance(),
            })

        return PaymentAllocationFormSet(request.POST or None, initial=initial)

    def post(self, request):
        header_form = PaymentGeneralForm(request.POST)

        if not header_form.is_valid():
            return render(request, self.template_name, {
                "header_form": header_form,
                "formset": None,
                "invoices": [],
            })

        client = header_form.cleaned_data["client"]
        payment_amount = Decimal(str(header_form.cleaned_data["amount"]))
        payment_date = header_form.cleaned_data["date"]
        payment_method = header_form.cleaned_data["method"]
        payment_memo = header_form.cleaned_data["memo"]

        invoices = [
            inv for inv in Invoice.objects.filter(client=client, status=InvoiceStatus.ISSUED)
            if inv.outstanding_balance() > 0
        ]

        formset = self._build_formset_with_initial(request, invoices)

        if not formset.is_valid():
            return render(request, self.template_name, {
                "header_form": header_form,
                "formset": formset,
                "invoices": invoices,
            })

        outstanding_by_id = {inv.id: inv for inv in invoices}
        allocation_list = []
        total_allocated = Decimal("0")

        for form in formset:
            cleaned = form.cleaned_data or {}
            invoice_id = cleaned.get("invoice_id")
            if not invoice_id:
                continue

            amt = Decimal(str(cleaned.get("amount_to_apply") or "0"))
            if amt == 0:
                continue

            inv = outstanding_by_id[invoice_id]
            outstanding = inv.outstanding_balance()

            if amt < 0:
                form.add_error("amount_to_apply", "Amount cannot be negative.")
                return render(request, self.template_name, {
                    "header_form": header_form,
                    "formset": formset,
                    "invoices": invoices,
                })

            if amt > outstanding:
                form.add_error(
                    "amount_to_apply",
                    f"Cannot apply more than outstanding balance ({outstanding})."
                )
                return render(request, self.template_name, {
                    "header_form": header_form,
                    "formset": formset,
                    "invoices": invoices,
                })

            allocation_list.append((inv, amt))
            total_allocated += amt

        if total_allocated > payment_amount:
            formset._non_form_errors = ErrorList([
                "Total applied cannot exceed payment amount."
            ])
            return render(request, self.template_name, {
                "header_form": header_form,
                "formset": formset,
                "invoices": invoices,
            })

        payment = Payment.objects.create(
            client=client,
            date=payment_date,
            amount=payment_amount,
            method=payment_method,
            memo=payment_memo,
            unapplied_amount=payment_amount,
        )

        for inv, amt in allocation_list:
            if amt > 0:
                PaymentApplication.objects.create(
                    payment=payment,
                    invoice=inv,
                    amount=amt,
                )
                inv.update_status()
                payment.unapplied_amount -= amt

        payment.save()
        payment.post_to_accounting(user=request.user)

        return redirect("accounting:payment_detail", pk=payment.id)


class PaymentCreateFromTransactionView(ReadOnlyUserMixin, View):
    template_name = "accounting/payment_from_transaction_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.txn = get_object_or_404(BankTransaction, pk=kwargs["txn_id"])
        self.bank_account = self.txn.bank_account
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, txn_id):
        form = PaymentGeneralForm(initial={
            "date": self.txn.date,
            "amount": abs(self.txn.amount),
            "memo": self.txn.description,
            "method": PaymentMethod.CHECK,
        })

        return render(request, self.template_name, {
            "header_form": form,
            "formset": None,
            "invoices": [],
            "txn": self.txn,
        })

    def post(self, request, txn_id):
        txn = self.txn
        header_form = PaymentGeneralForm(request.POST)

        if not header_form.is_valid():
            return render(request, self.template_name, {
                "header_form": header_form,
                "formset": None,
                "invoices": [],
                "txn": txn,
            })

        client = header_form.cleaned_data["client"]
        payment_amount = Decimal(str(header_form.cleaned_data["amount"]))
        payment_date = header_form.cleaned_data["date"]
        payment_method = header_form.cleaned_data["method"]
        payment_memo = header_form.cleaned_data["memo"]

        invoices = [
            inv for inv in Invoice.objects.filter(client=client, status=InvoiceStatus.ISSUED)
            if inv.outstanding_balance() > 0
        ]

        if not invoices:
            payment = Payment.objects.create(
                client=client,
                date=payment_date,
                amount=payment_amount,
                method=payment_method,
                memo=payment_memo,
                unapplied_amount=payment_amount,
            )
            payment.post_to_accounting(user=request.user)
            BankTransactionService.link_existing_payment(txn, payment)
            messages.success(request, "Payment created and linked to transaction.")
            return redirect("accounting:bankaccount_register", pk=self.bank_account.pk)

        formset = build_formset(request, invoices)

        if not formset.is_valid():
            return render(request, self.template_name, {
                "header_form": header_form,
                "formset": formset,
                "invoices": invoices,
                "txn": txn,
            })

        outstanding_by_id = {inv.id: inv for inv in invoices}
        allocation_list = []
        total_allocated = Decimal("0")

        for form in formset:
            cleaned = form.cleaned_data or {}
            invoice_id = cleaned.get("invoice_id")
            if not invoice_id:
                continue

            amt = Decimal(str(cleaned.get("amount_to_apply") or "0"))
            if amt == 0:
                continue

            inv = outstanding_by_id[invoice_id]
            if amt < 0:
                form.add_error("amount_to_apply", "Amount cannot be negative.")
                return render(request, self.template_name, {
                    "header_form": header_form,
                    "formset": formset,
                    "invoices": invoices,
                    "txn": txn,
                })

            if amt > inv.outstanding_balance():
                form.add_error("amount_to_apply", "Cannot exceed outstanding balance.")
                return render(request, self.template_name, {
                    "header_form": header_form,
                    "formset": formset,
                    "invoices": invoices,
                    "txn": txn,
                })

            allocation_list.append((inv, amt))
            total_allocated += amt

        if total_allocated > payment_amount:
            formset._non_form_errors = ErrorList(["Total applied cannot exceed payment amount."])
            return render(request, self.template_name, {
                "header_form": header_form,
                "formset": formset,
                "invoices": invoices,
                "txn": txn,
            })

        payment = Payment.objects.create(
            client=client,
            date=payment_date,
            amount=payment_amount,
            method=payment_method,
            memo=payment_memo,
            unapplied_amount=payment_amount,
        )

        for inv, amt in allocation_list:
            if amt > 0:
                PaymentApplication.objects.create(
                    payment=payment,
                    invoice=inv,
                    amount=amt,
                )
                inv.update_status()
                payment.unapplied_amount -= amt

        payment.save()
        payment.post_to_accounting(user=request.user)
        BankTransactionService.link_existing_payment(txn, payment)

        messages.success(request, "Payment created and linked to transaction.")
        return redirect("accounting:bankaccount_register", pk=self.bank_account.pk)


class PaymentCreateForInvoiceView(ReadOnlyUserMixin, FormView):
    template_name = "accounting/payment_for_invoice_form.html"
    form_class = PaymentForInvoiceForm

    def dispatch(self, request, *args, **kwargs):
        self.invoice = get_object_or_404(Invoice, id=kwargs["invoice_id"])
        self.client = self.invoice.client
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["client"] = self.client
        return kwargs

    def form_valid(self, form):
        payment = Payment.objects.create(
            client=self.client,
            date=form.cleaned_data["date"],
            amount=form.cleaned_data["amount"],
            unapplied_amount=form.cleaned_data["amount"],
            method=form.cleaned_data["method"],
            memo=form.cleaned_data["memo"],
        )

        amount_to_apply = min(payment.unapplied_amount, self.invoice.outstanding_balance())

        PaymentApplication.objects.create(
            payment=payment,
            invoice=self.invoice,
            amount=amount_to_apply,
        )
        self.invoice.update_status()
        payment.unapplied_amount -= amount_to_apply
        payment.save()
        payment.post_to_accounting(user=self.request.user)

        self.payment = payment
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("billing:invoice_detail", args=[self.invoice.id])


def payment_invoice_fragment(request):
    """Returns invoice allocation rows for AJAX/htmx."""
    client_id = request.GET.get("client")

    if not client_id:
        return render(request, "accounting/payment_invoice_rows.html", {
            "invoices": None,
            "formset": None,
        })

    invoices = [
        inv for inv in Invoice.objects.filter(client_id=client_id, status=InvoiceStatus.ISSUED)
        if inv.outstanding_balance() > 0
    ]

    PaymentAllocationFormSetLocal = formset_factory(PaymentAllocationForm, extra=0)

    initial = [{
        "invoice_id": inv.id,
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.issue_date,
        "original_amount": inv.total,
        "outstanding_balance": inv.outstanding_balance(),
        "amount_to_apply": Decimal("0.00")
    } for inv in invoices]

    formset = PaymentAllocationFormSetLocal(initial=initial)

    return render(
        request,
        "accounting/payment_invoice_rows.html",
        {
            "invoices": invoices,
            "formset": formset,
        },
    )
