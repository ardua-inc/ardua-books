"""
Invoice management views.
"""
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.core.mail import EmailMessage
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, get_object_or_404, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import ListView, CreateView, UpdateView, DetailView, DeleteView, TemplateView

from billing.models import (
    Client,
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
    InvoiceEmailForm,
)
from billing.services import (
    attach_unbilled_items_to_invoice,
    detach_invoice_lines,
    mark_all_te_ex_unbilled_and_unlink,
    mark_te_ex_unbilled_keep_invoice_lines,
)
from accounting.models import JournalEntry
from accounting.views.mixins import FilterPersistenceMixin, ReadOnlyUserMixin


class InvoiceListView(FilterPersistenceMixin, LoginRequiredMixin, TemplateView):
    template_name = "billing/invoice_list.html"

    # Filter persistence
    filter_persistence_key = "invoice_list_filters"
    filter_params = ["client", "status", "date_preset", "date_from", "date_to", "per_page"]

    DEFAULT_PAGE_SIZE = 25
    PAGE_SIZE_OPTIONS = [10, 25, 50, 100]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = date.today()

        # Get filter parameters
        client_filter = self.request.GET.get("client", "")
        status_filter = self.request.GET.get("status", "")
        date_preset = self.request.GET.get("date_preset", "")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

        # Build queryset
        qs = Invoice.objects.select_related("client").order_by("-issue_date", "-id")

        # Apply client filter
        if client_filter:
            qs = qs.filter(client_id=client_filter)

        # Apply status filter
        if status_filter:
            qs = qs.filter(status=status_filter)

        # Determine date range
        if date_preset == "mtd":
            from_date = today.replace(day=1)
            to_date = today
        elif date_preset == "ytd":
            from_date = today.replace(month=1, day=1)
            to_date = today
        elif date_preset == "last_year":
            from_date = date(today.year - 1, 1, 1)
            to_date = date(today.year - 1, 12, 31)
        elif date_preset == "last30":
            from_date = today - timedelta(days=30)
            to_date = today
        elif date_preset == "last90":
            from_date = today - timedelta(days=90)
            to_date = today
        elif date_from or date_to:
            from_date = date.fromisoformat(date_from) if date_from else None
            to_date = date.fromisoformat(date_to) if date_to else None
            date_preset = ""
        else:
            from_date = None
            to_date = None

        # Apply date filters
        if from_date:
            qs = qs.filter(issue_date__gte=from_date)
        if to_date:
            qs = qs.filter(issue_date__lte=to_date)

        # Pagination
        page_size = self.request.GET.get("per_page", self.DEFAULT_PAGE_SIZE)
        try:
            page_size = int(page_size)
            if page_size not in self.PAGE_SIZE_OPTIONS:
                page_size = self.DEFAULT_PAGE_SIZE
        except (ValueError, TypeError):
            page_size = self.DEFAULT_PAGE_SIZE

        paginator = Paginator(qs, page_size)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        ctx.update({
            "invoices": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "clients": Client.objects.all().order_by("name"),
            "client_filter": client_filter,
            "status_choices": InvoiceStatus.choices,
            "status_filter": status_filter,
            "date_preset": date_preset,
            "date_from": date_from if not date_preset else "",
            "date_to": date_to if not date_preset else "",
            "per_page": page_size,
            "page_size_options": self.PAGE_SIZE_OPTIONS,
            "InvoiceStatus": InvoiceStatus,
        })
        return ctx


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


class InvoiceCreateView(ReadOnlyUserMixin, LoginRequiredMixin, CreateView):
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


class InvoiceUpdateView(ReadOnlyUserMixin, LoginRequiredMixin, UpdateView):
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
            queryset=invoice.lines.filter(
                line_type__in=[
                    InvoiceLine.LineType.GENERAL,
                    InvoiceLine.LineType.ADJUSTMENT,
                ]
            ),
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


class InvoiceChangeStatusView(ReadOnlyUserMixin, LoginRequiredMixin, View):
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


class InvoiceDeleteView(ReadOnlyUserMixin, LoginRequiredMixin, DeleteView):
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


@login_required
def invoice_email_view(request, pk):
    """Email an invoice PDF to the client."""
    invoice = get_object_or_404(Invoice, pk=pk)

    # Only allow emailing Issued or Paid invoices
    if invoice.status not in (InvoiceStatus.ISSUED, InvoiceStatus.PAID):
        messages.error(request, "Only issued or paid invoices can be emailed.")
        return redirect("billing:invoice_detail", pk=invoice.pk)

    if request.method == "POST":
        form = InvoiceEmailForm(request.POST)
        if form.is_valid():
            to_email = form.cleaned_data["to_email"]
            subject = form.cleaned_data["subject"]
            custom_message = form.cleaned_data["message"]

            # Build email body
            body = f"Please find attached Invoice {invoice.invoice_number}."
            if custom_message:
                body += f"\n\n{custom_message}"
            body += "\n\n"  # Add spacing before attachment

            # Generate PDF (reuse logic from pdf_views)
            from billing.views.pdf_views import _generate_invoice_pdf
            pdf_bytes = _generate_invoice_pdf(invoice, request)
            filename = f"Ardua Inc - Invoice {invoice.invoice_number}.pdf"

            # Send email
            email = EmailMessage(
                subject=subject,
                body=body,
                to=[to_email],
            )
            email.attach(filename, pdf_bytes, "application/pdf")

            try:
                email.send()
                messages.success(
                    request,
                    f"Invoice emailed successfully to {to_email}."
                )
            except Exception as e:
                messages.error(request, f"Failed to send email: {e}")

            return redirect("billing:invoice_detail", pk=invoice.pk)
    else:
        # Pre-fill form with defaults
        form = InvoiceEmailForm(initial={
            "to_email": invoice.client.email,
            "subject": f"Invoice {invoice.invoice_number} from Ardua, Inc",
        })

    return render(request, "billing/invoice_email_form.html", {
        "form": form,
        "invoice": invoice,
    })
