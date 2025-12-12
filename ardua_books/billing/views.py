from datetime import timedelta,date
from decimal import Decimal, InvalidOperation

import io
from mimetypes import guess_type

from pypdf import PdfReader, PdfWriter
import weasyprint

from . import views

import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Sum
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden, request
from django.shortcuts import redirect, get_object_or_404, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy, reverse
from django.utils.dateparse import parse_date
from django.views import View
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.generic import (
    ListView,
    CreateView,
    UpdateView,
    DetailView,
    DeleteView,
)

from .models import (
    Client,
    Consultant,
    TimeEntry,
    Expense,
    ExpenseCategory,
    Invoice,
    InvoiceLine,
    BillableStatus,
    InvoiceStatus,
)

from .forms import (
    ClientForm,
    TimeEntryForm,
    ExpenseForm,
    InvoiceCreateForm,
    InvoiceUpdateForm,
    CreateInvoiceLineFormSet,
    UpdateInvoiceLineFormSet,
)

from .services import (
    attach_unbilled_items_to_invoice, 
    detach_invoice_lines,
    mark_all_te_ex_unbilled_and_unlink,
    mark_te_ex_unbilled_keep_invoice_lines,
)

from accounting.models import (
    JournalEntry,
    Payment,
    PaymentApplication,
)


class ClientListView(LoginRequiredMixin, ListView):
    model = Client
    template_name = "billing/client_list.html"
    context_object_name = "clients"

    def get_queryset(self):
        qs = super().get_queryset()
        show_inactive = self.request.GET.get("show_inactive")
        if show_inactive:
            return qs.order_by("name")
        return qs.filter(is_active=True).order_by("name")


class ClientCreateView(LoginRequiredMixin, CreateView):
    model = Client
    form_class = ClientForm
    template_name = "billing/client_form.html"
    success_url = reverse_lazy("billing:client_list")

class ClientDetailView(DetailView):
    model = Client
    template_name = "billing/client_detail.html"
    context_object_name = "client"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        client = self.object

        invoices = Invoice.objects.filter(client=client).order_by("-issue_date")

        context.update({
            "invoices": invoices,
        })
        return context


class ClientUpdateView(LoginRequiredMixin, UpdateView):
    model = Client
    form_class = ClientForm
    template_name = "billing/client_form.html"
    success_url = reverse_lazy("billing:client_list")

class ClientFinancialView(DetailView):
    model = Client
    template_name = "billing/client_financial.html"
    context_object_name = "client"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        client = self.object

        # Outstanding invoices
        invoices = Invoice.objects.filter(client=client).order_by("due_date")

        outstanding_total = sum(inv.outstanding_balance() for inv in invoices)

        # All payments for this client
        payments = Payment.objects.filter(client=client).order_by("-date")

        unapplied_total = sum(p.unapplied_amount for p in payments)

        # Build tables
        outstanding_invoices = [
            {
                "invoice": inv,
                "total": inv.total,
                "applied": inv.applied_payments_total(),
                "outstanding": inv.outstanding_balance(),
            }
            for inv in invoices
            if inv.outstanding_balance() > 0
        ]

        unapplied_payments = [
            p for p in payments if p.unapplied_amount > 0
        ]

        # Net client position
        net_position = outstanding_total - unapplied_total

        context.update({
            "outstanding_total": outstanding_total,
            "unapplied_total": unapplied_total,
            "net_position": net_position,
            "outstanding_invoices": outstanding_invoices,
            "unapplied_payments": unapplied_payments,
        })

        return context

class TimeEntryListView(LoginRequiredMixin, ListView):
    model = TimeEntry
    template_name = "billing/timeentry_list.html"
    context_object_name = "time_entries"

    def get_queryset(self):
        qs = super().get_queryset()
        # We'll adjust ordering in the next section
        return qs.select_related("client", "consultant").order_by("-work_date", "-created_at")


class TimeEntryCreateView(LoginRequiredMixin, CreateView):
    model = TimeEntry
    form_class = TimeEntryForm
    template_name = "billing/timeentry_form.html"
    success_url = reverse_lazy("billing:timeentry_list")

    def form_valid(self, form):
        """
        If billing_rate is left blank, default it from the client's default_hourly_rate.
        """
        billing_rate = form.cleaned_data.get("billing_rate")
        client = form.cleaned_data.get("client")

        if (billing_rate is None or billing_rate == 0) and client and client.default_hourly_rate is not None:
            form.instance.billing_rate = client.default_hourly_rate

        return super().form_valid(form)
    
class TimeEntryUpdateView(LoginRequiredMixin, UpdateView):
    model = TimeEntry
    form_class = TimeEntryForm
    template_name = "billing/timeentry_form.html"
    success_url = reverse_lazy("billing:timeentry_list")

    def get_queryset(self):
        # Optionally enforce per-user visibility if you do that elsewhere
        qs = super().get_queryset()
        return qs.select_related("client", "consultant")

class ExpenseListView(LoginRequiredMixin, ListView):
    model = Expense
    template_name = "billing/expense_list.html"
    context_object_name = "expenses"

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.select_related("client", "category").order_by("-expense_date", "-created_at")


class ExpenseCreateView(LoginRequiredMixin, CreateView):
    model = Expense
    form_class = ExpenseForm
    template_name = "billing/expense_form.html"
    success_url = reverse_lazy("billing:expense_list")

    def form_valid(self, form):
        # If billable is left checked but no client is selected, you may want to force billable=False.
        if form.cleaned_data.get("billable") and not form.cleaned_data.get("client"):
            form.instance.billable = False
        return super().form_valid(form)
    
class ExpenseDetailView(LoginRequiredMixin, DetailView):
    model = Expense
    template_name = "billing/expense_detail.html"
    context_object_name = "expense"

class ExpenseUpdateView(LoginRequiredMixin, UpdateView):
    model = Expense
    form_class = ExpenseForm
    template_name = "billing/expense_form.html"
    success_url = reverse_lazy("billing:expense_list")

    def form_valid(self, form):
        if form.cleaned_data.get("billable") and not form.cleaned_data.get("client"):
            form.instance.billable = False
        return super().form_valid(form)

class ExpenseDeleteView(LoginRequiredMixin, DeleteView):
    model = Expense
    template_name = "billing/expense_confirm_delete.html"
    success_url = reverse_lazy("billing:expense_list")

    
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

        # journal entries
        ct = ContentType.objects.get_for_model(invoice)
        journal_entries = JournalEntry.objects.filter(
            source_content_type=ct,
            source_object_id=invoice.id,
        ).order_by("posted_at")
        context["journal_entries"] = journal_entries

        # Status buttons
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
                queryset=InvoiceLine.objects.none(),  # CRITICAL
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
            queryset=InvoiceLine.objects.none(),  # CRITICAL
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
        detach_invoice_lines(invoice, detach_ids)

        invoice.recalculate_totals()
        return redirect("billing:invoice_detail", pk=invoice.pk)


# class InvoiceChangeStatusView(LoginRequiredMixin, View):
#     ALLOWED_ACTIONS = {"issue", "void", "pay", "return_to_draft"}

#     def post(self, request, *args, **kwargs):
#         # Buttons can submit via POST; we just route to the same logic.
#         return self.get(request, *args, **kwargs)

#     def get(self, request, *args, **kwargs):
#         invoice = get_object_or_404(Invoice, pk=kwargs["pk"])
#         action = kwargs["action"]

#         if action not in self.ALLOWED_ACTIONS:
#             messages.error(request, "Invalid status operation.")
#             return redirect("billing:invoice_detail", pk=invoice.pk)

#         orig_status = invoice.status

#         from accounting.services import post_invoice, reverse_invoice

#         # ------------------
#         # Status transitions
#         # ------------------

#         if action == "issue" and invoice.status == InvoiceStatus.DRAFT:
#             invoice.status = InvoiceStatus.ISSUED
#             invoice.save()
#             post_invoice(invoice, request.user)
#             messages.success(request, "Invoice issued.")

#         elif action == "return_to_draft" and invoice.status == InvoiceStatus.ISSUED:
#             # NEW: enforce “only one draft per client” rule *before* saving
#             if invoice.other_draft_exists:
#                 messages.error(
#                     request,
#                     (
#                         "This invoice cannot be returned to Draft because another "
#                         "draft invoice already exists for this client. "
#                         "Please issue or delete the existing draft first."
#                     ),
#                 )
#                 return redirect("billing:invoice_detail", pk=invoice.pk)

#             # Safe to reverse and return to draft
#             reverse_invoice(invoice, request.user)
#             invoice.status = InvoiceStatus.DRAFT
#             invoice.save()
#             messages.success(request, "Invoice returned to draft.")

#         elif action == "pay" and invoice.status == InvoiceStatus.ISSUED:
#             invoice.status = InvoiceStatus.PAID
#             invoice.save()
#             messages.success(request, "Invoice marked paid.")

#         elif action == "void" and invoice.status in [
#             InvoiceStatus.DRAFT,
#             InvoiceStatus.ISSUED,
#         ]:
#             if invoice.status == InvoiceStatus.ISSUED:
#                 reverse_invoice(invoice, request.user)
#             invoice.status = InvoiceStatus.VOID
#             invoice.save()
#             messages.success(request, "Invoice voided.")

#         else:
#             messages.error(request, "That operation is not allowed.")
#             return redirect("billing:invoice_detail", pk=invoice.pk)

#         # -----------------------------
#         # Mark lines as billed on ISSUE
#         # -----------------------------
#         if orig_status != InvoiceStatus.ISSUED and invoice.status == InvoiceStatus.ISSUED:
#             for line in invoice.lines.all():
#                 if line.line_type == InvoiceLine.LineType.TIME:
#                     timeentry = getattr(line, "timeentry", None)
#                     if timeentry and timeentry.status == BillableStatus.UNBILLED:
#                         timeentry.status = BillableStatus.BILLED
#                         timeentry.save()

#                 elif line.line_type == InvoiceLine.LineType.EXPENSE:
#                     expense = getattr(line, "expense", None)
#                     if expense and expense.status == BillableStatus.UNBILLED:
#                         expense.status = BillableStatus.BILLED
#                         expense.save()

#         return redirect("billing:invoice_detail", pk=invoice.pk)

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

        from accounting.services import post_invoice, reverse_invoice

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

        # Only allow deleting DRAFT invoices
        if self.object.status != InvoiceStatus.DRAFT:
            return HttpResponseForbidden("Only draft invoices can be deleted.")

        # For each attached line, release the linkage and reset status to UNBILLED
        for line in self.object.lines.all():
            if line.time_entry_id:
                te = line.time_entry
                te.invoice_line = None
                # If somehow marked BILLED while invoice is still DRAFT, revert it
                if te.status == BillableStatus.BILLED:
                    te.status = BillableStatus.UNBILLED
                te.save(update_fields=["invoice_line", "status"])

            if line.expense_id:
                e = line.expense
                e.invoice_line = None
                if e.status == BillableStatus.BILLED:
                    e.status = BillableStatus.UNBILLED
                e.save(update_fields=["invoice_line", "status"])

        # Now delete the invoice itself
        return super().delete(request, *args, **kwargs)


@login_required
def invoice_print_view(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    return render(request, "billing/invoice_print.html", {"invoice": invoice,  "embed_receipts": True})

@login_required
def invoice_print_pdf(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)

    # Render the existing invoice HTML, but WITHOUT embedded receipts
    html = render_to_string(
        "billing/invoice_print.html",
        {
            "invoice": invoice,
            "embed_receipts": False,  # we’ll add this flag in the template
        },
        request=request,
    )

    # Convert invoice HTML to a standalone PDF (invoice only)
    base_pdf_bytes = weasyprint.HTML(
        string=html,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf()

    writer = PdfWriter()

    # Add all pages of the invoice PDF
    base_reader = PdfReader(io.BytesIO(base_pdf_bytes))
    for page in base_reader.pages:
        writer.add_page(page)

    # Append each PDF receipt as full pages
    for line in invoice.lines.all():
        expense = getattr(line, "expense", None)
        if not expense or not expense.receipt:
            continue

        path = expense.receipt.path
        mime, _ = guess_type(path)

        # Case 1: PDF receipt – append pages directly
        if mime == "application/pdf":
            receipt_reader = PdfReader(path)
            for page in receipt_reader.pages:
                writer.add_page(page)

        # Case 2: Image receipt – render a simple HTML page with the image,
        # convert to PDF, then append that page.
        elif mime and mime.startswith("image/"):
            img_html = render_to_string(
                "billing/receipt_image_page.html",
                {"expense": expense},
                request=request,
            )
            img_pdf_bytes = weasyprint.HTML(
                string=img_html,
                base_url=request.build_absolute_uri("/"),
            ).write_pdf()

            img_reader = PdfReader(io.BytesIO(img_pdf_bytes))
            for page in img_reader.pages:
                writer.add_page(page)

    # Other types (e.g., unknown mime) are ignored for now.


    output = io.BytesIO()
    writer.write(output)
    output.seek(0)

    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'inline; filename="invoice-{invoice.invoice_number}.pdf"'
    )
    return response

### Mobile entry
@login_required
def mobile_home(request):
    # Render a very simple shell template with the PWA hooks (see section 3)
    context = {"mobile": True}
    return render(request, "billing/mobile_home.html", context)

@login_required
def mobile_time_list(request):
    """
    Simple recent time-entry list for mobile.
    For now: show the last 50 entries, ordered by work_date.
    If/when you want per-consultant filtering, we can
    refine this using request.user.consultant.
    """
    qs = (
        TimeEntry.objects
        .select_related("client", "consultant")
        .order_by("-work_date", "-created_at")
    )
    entries = qs[:50]

    context = {
        "mobile": True,
        "time_entries": entries,
    }
    return render(request, "billing/mobile_time_list.html", context)


@login_required
def mobile_expense_list(request):
    """
    Simple recent expense list for mobile.
    Show the last 50 expenses, newest first.
    """
    qs = (
        Expense.objects
        .select_related("client", "category")
        .order_by("-expense_date", "-created_at")
    )
    expenses = qs[:50]

    context = {
        "mobile": True,
        "expenses": expenses,
    }
    return render(request, "billing/mobile_expense_list.html", context)

@login_required
@require_POST
def mobile_time_entry_create(request):
    """
    Create a TimeEntry from a JSON payload posted by the mobile PWA.

    Expected JSON:
      {
        "date": "YYYY-MM-DD",
        "hours": "4",
        "description": "Some text",
        "client_id": 123        # optional; if omitted, default client used
      }
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload"}, status=400)

    # Date
    work_date = parse_date(data.get("date") or "") or date.today()

    # Hours
    raw_hours = data.get("hours")
    try:
        hours = Decimal(str(raw_hours))
    except (InvalidOperation, TypeError):
        return JsonResponse({"error": f"Invalid hours value: {raw_hours!r}"}, status=400)

    description = (data.get("description") or "").strip()

    # Consultant tied to logged-in user
    try:
        consultant = Consultant.objects.get(user=request.user)
    except Consultant.DoesNotExist:
        return JsonResponse(
            {"error": "No Consultant is linked to this user. Create one in admin and link it to your user."},
            status=400,
        )

    # Client: use client_id if passed, else default to first active
    client_id = data.get("client_id")
    client = None
    if client_id:
        try:
            client = Client.objects.get(pk=client_id)
        except Client.DoesNotExist:
            return JsonResponse({"error": f"Client {client_id} not found."}, status=400)
    else:
        client = Client.objects.filter(is_active=True).order_by("name").first() or Client.objects.first()

    if not client:
        return JsonResponse(
            {"error": "No Client exists. Create a client first in the desktop app."},
            status=400,
        )

    billing_rate = (
        consultant.default_hourly_rate
        or client.default_hourly_rate
        or Decimal("0.00")
    )

    te = TimeEntry.objects.create(
        client=client,
        consultant=consultant,
        work_date=work_date,
        hours=hours,
        description=description,
        billing_rate=billing_rate,
        # status defaults to UNBILLED
    )

    return JsonResponse({"ok": True, "id": te.pk})

@login_required
@require_POST
def mobile_expense_create(request):
    """
    Create an Expense from a JSON payload posted by the mobile PWA.

    Expected JSON:
      {
        "date": "YYYY-MM-DD",
        "amount": "123.45",
        "description": "Some text",
        "client_id": 123,       # optional; if omitted, default client used
        "category_id": 45       # optional; if omitted, default category used
      }
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload"}, status=400)

    # Date
    expense_date = parse_date(data.get("date") or "") or date.today()

    # Amount
    raw_amount = data.get("amount")
    try:
        amount = Decimal(str(raw_amount))
    except (InvalidOperation, TypeError):
        return JsonResponse({"error": f"Invalid amount value: {raw_amount!r}"}, status=400)

    description = (data.get("description") or "").strip()

    # Client
    client_id = data.get("client_id")
    client = None
    if client_id:
        try:
            client = Client.objects.get(pk=client_id)
        except Client.DoesNotExist:
            return JsonResponse({"error": f"Client {client_id} not found."}, status=400)
    else:
        client = Client.objects.filter(is_active=True).order_by("name").first() or Client.objects.first()
    if not client:
        return JsonResponse(
            {"error": "No Client exists. Create a client first in the desktop app."},
            status=400,
        )

    # Category
    category_id = data.get("category_id")
    category = None
    if category_id:
        try:
            category = ExpenseCategory.objects.get(pk=category_id)
        except ExpenseCategory.DoesNotExist:
            return JsonResponse({"error": f"Category {category_id} not found."}, status=400)
    else:
        category = ExpenseCategory.objects.order_by("name").first()
    if not category:
        return JsonResponse(
            {"error": "No ExpenseCategory exists. Create one in the desktop app."},
            status=400,
        )

    expense = Expense.objects.create(
        client=client,
        category=category,
        expense_date=expense_date,
        amount=amount,
        description=description,
        billable=True,
        # status defaults to UNBILLED
    )

    return JsonResponse({"ok": True, "id": expense.pk})

@login_required
def mobile_meta(request):
    """
    Return basic metadata for the mobile app:
    - Active clients
    - Expense categories

    Used by JS to populate dropdowns.
    """
    clients = list(
        Client.objects
        .filter(is_active=True)
        .order_by("name")
        .values("id", "name")
    )
    categories = list(
        ExpenseCategory.objects
        .order_by("name")
        .values("id", "name")
    )
    return JsonResponse({"clients": clients, "categories": categories})

@login_required
def invoice_unbilled_fragment(request):
    client_id = request.GET.get("client")

    if not client_id:
        return render(request, "billing/invoice_unbilled_fragment.html", {
            "client": None
        })

    # Fetch unbilled items
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

    # Compute totals
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