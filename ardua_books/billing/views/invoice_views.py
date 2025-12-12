"""
Invoice management views.
"""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, get_object_or_404, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import ListView, CreateView, UpdateView, DetailView, DeleteView

from billing.models import (
    TimeEntry,
    Expense,
    Invoice,
    InvoiceLine,
    BillableStatus,
    InvoiceStatus,
)
from billing.forms import (
    InvoiceCreateForm,
    InvoiceUpdateForm,
    CreateInvoiceLineFormSet,
    UpdateInvoiceLineFormSet,
)
from billing.services import (
    attach_unbilled_items_to_invoice,
    detach_invoice_lines,
    mark_all_te_ex_unbilled_and_unlink,
    mark_te_ex_unbilled_keep_invoice_lines,
)
from accounting.models import JournalEntry


class InvoiceListView(LoginRequiredMixin, ListView):
    model = Invoice
    template_name = "billing/invoice_list.html"
    context_object_name = "invoices"
    ordering = ["-issue_date", "-id"]


class InvoiceDetailView(LoginRequiredMixin, DetailView):
    model = Invoice
    template_name = "billing/invoice_view.html"
    context_object_name = "invoice"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        invoice = self.object

        ct = ContentType.objects.get_for_model(invoice)
        journal_entries = JournalEntry.objects.filter(
            source_content_type=ct,
            source_object_id=invoice.id,
        ).order_by("posted_at")
        context["journal_entries"] = journal_entries
        context["InvoiceStatus"] = InvoiceStatus

        return context


class InvoiceCreateView(LoginRequiredMixin, CreateView):
    model = Invoice
    form_class = InvoiceCreateForm
    template_name = "billing/invoice_create.html"
    context_object_name = "invoice"

    def get_initial(self):
        initial = super().get_initial()
        client_id = self.request.GET.get("client")
        if client_id:
            initial["client"] = client_id
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        invoice = ctx["form"].instance

        if "formset" not in ctx:
            ctx["formset"] = CreateInvoiceLineFormSet(
                instance=invoice,
                queryset=InvoiceLine.objects.none(),
            )
        return ctx

    @transaction.atomic
    def form_valid(self, form):
        invoice = form.save(commit=False)
        invoice.status = InvoiceStatus.DRAFT

        if not invoice.due_date and invoice.client and invoice.issue_date:
            invoice.due_date = invoice.issue_date + timedelta(
                days=invoice.client.payment_terms_days
            )
        invoice.save()

        formset = CreateInvoiceLineFormSet(
            self.request.POST,
            instance=invoice,
            queryset=InvoiceLine.objects.none(),
        )
        if not formset.is_valid():
            ctx = self.get_context_data(form=form)
            ctx["formset"] = formset
            return self.render_to_response(ctx)
        formset.save()

        time_ids = [int(x) for x in self.request.POST.getlist("time_ids")]
        expense_ids = [int(x) for x in self.request.POST.getlist("expense_ids")]
        attach_unbilled_items_to_invoice(invoice, time_ids, expense_ids)

        invoice.recalculate_totals()
        return redirect("billing:invoice_detail", pk=invoice.pk)


class InvoiceUpdateView(LoginRequiredMixin, UpdateView):
    model = Invoice
    form_class = InvoiceUpdateForm
    template_name = "billing/invoice_update.html"
    context_object_name = "invoice"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        invoice = self.object
        client = invoice.client

        if "formset" not in ctx:
            ctx["formset"] = UpdateInvoiceLineFormSet(
                instance=invoice,
                queryset=invoice.lines.filter(
                    line_type__in=[
                        InvoiceLine.LineType.GENERAL,
                        InvoiceLine.LineType.ADJUSTMENT,
                    ]
                ),
                prefix="lines",
            )

        ctx["attached_time"] = invoice.lines.filter(line_type=InvoiceLine.LineType.TIME)
        ctx["attached_expenses"] = invoice.lines.filter(line_type=InvoiceLine.LineType.EXPENSE)

        ctx["unbilled_time"] = TimeEntry.objects.filter(
            client=client,
            status=BillableStatus.UNBILLED,
            invoice_line__isnull=True,
        )
        ctx["unbilled_expenses"] = Expense.objects.filter(
            client=client,
            billable=True,
            status=BillableStatus.UNBILLED,
            invoice_line__isnull=True,
        )
        ctx["total_time_value"] = sum(
            te.hours * te.billing_rate for te in ctx["unbilled_time"]
        )
        ctx["total_expense_value"] = sum(
            ex.amount for ex in ctx["unbilled_expenses"]
        )
        ctx["subtotal"] = ctx["total_time_value"] + ctx["total_expense_value"]

        return ctx

    @transaction.atomic
    def form_valid(self, form):
        invoice = form.save()

        formset = UpdateInvoiceLineFormSet(
            self.request.POST,
            instance=invoice,
            prefix="lines",
        )
        if not formset.is_valid():
            return self.render_to_response(
                self.get_context_data(form=form, formset=formset)
            )
        formset.save()

        time_ids = [int(x) for x in self.request.POST.getlist("time_ids")]
        expense_ids = [int(x) for x in self.request.POST.getlist("expense_ids")]
        attach_unbilled_items_to_invoice(invoice, time_ids, expense_ids)

        detach_ids = [int(x) for x in self.request.POST.getlist("detach_ids")]
        if detach_ids:
            if invoice.status != InvoiceStatus.DRAFT:
                raise ValidationError("Only draft invoices may detach line items.")
            detach_invoice_lines(invoice, detach_ids)

        invoice.recalculate_totals()
        return redirect("billing:invoice_detail", pk=invoice.pk)


class InvoiceChangeStatusView(LoginRequiredMixin, View):
    ALLOWED_ACTIONS = {"issue", "void", "pay", "return_to_draft"}

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        invoice = get_object_or_404(Invoice, pk=kwargs["pk"])
        action = kwargs["action"]

        if action not in self.ALLOWED_ACTIONS:
            messages.error(request, "Invalid status operation.")
            return redirect("billing:invoice_detail", pk=invoice.pk)

        from accounting.services.posting import post_invoice, reverse_invoice

        if action == "issue" and invoice.status == InvoiceStatus.DRAFT:
            invoice.status = InvoiceStatus.ISSUED
            invoice.save()
            post_invoice(invoice, request.user)
            messages.success(request, "Invoice issued.")

        elif action == "return_to_draft" and invoice.status == InvoiceStatus.ISSUED:
            if invoice.other_draft_exists:
                messages.error(
                    request,
                    "Another draft invoice already exists for this client."
                )
                return redirect("billing:invoice_detail", pk=invoice.pk)

            reverse_invoice(invoice, request.user)
            mark_te_ex_unbilled_keep_invoice_lines(invoice)
            invoice.status = InvoiceStatus.DRAFT
            invoice.save()
            messages.success(request, "Invoice returned to draft.")

        elif action == "pay" and invoice.status == InvoiceStatus.ISSUED:
            invoice.status = InvoiceStatus.PAID
            invoice.save()
            messages.success(request, "Invoice marked paid.")

        elif action == "void" and invoice.status in {InvoiceStatus.DRAFT, InvoiceStatus.ISSUED}:
            if invoice.status == InvoiceStatus.ISSUED:
                reverse_invoice(invoice, request.user)

            mark_all_te_ex_unbilled_and_unlink(invoice)
            invoice.status = InvoiceStatus.VOID
            invoice.save()
            messages.success(request, "Invoice voided.")

        else:
            messages.error(request, "That operation is not allowed.")

        return redirect("billing:invoice_detail", pk=invoice.pk)


class InvoiceDeleteView(LoginRequiredMixin, DeleteView):
    model = Invoice
    template_name = "billing/invoice_confirm_delete.html"
    success_url = reverse_lazy("billing:invoice_list")

    @transaction.atomic
    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()

        if self.object.status != InvoiceStatus.DRAFT:
            return HttpResponseForbidden("Only draft invoices can be deleted.")

        for line in self.object.lines.all():
            if line.time_entry_id:
                te = line.time_entry
                te.invoice_line = None
                if te.status == BillableStatus.BILLED:
                    te.status = BillableStatus.UNBILLED
                te.save(update_fields=["invoice_line", "status"])

            if line.expense_id:
                e = line.expense
                e.invoice_line = None
                if e.status == BillableStatus.BILLED:
                    e.status = BillableStatus.UNBILLED
                e.save(update_fields=["invoice_line", "status"])

        return super().delete(request, *args, **kwargs)
