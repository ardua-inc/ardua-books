"""
Client management views.
"""
from datetime import date
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DetailView

from billing.models import Client, Invoice, InvoiceStatus
from billing.forms import ClientForm
from accounting.models import Payment


# Default pagination settings (can be used as pattern for other views)
DEFAULT_PAGE_SIZE = 20
PAGE_SIZE_OPTIONS = [10, 20, 50, 100]


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


class ClientDetailView(LoginRequiredMixin, DetailView):
    model = Client
    template_name = "billing/client_detail.html"
    context_object_name = "client"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        client = self.object

        # Base queryset
        invoices = Invoice.objects.filter(client=client)

        # Status filter
        status_filter = self.request.GET.get("status", "active")
        if status_filter == "draft":
            invoices = invoices.filter(status=InvoiceStatus.DRAFT)
        elif status_filter == "issued":
            invoices = invoices.filter(status=InvoiceStatus.ISSUED)
        elif status_filter == "paid":
            invoices = invoices.filter(status=InvoiceStatus.PAID)
        elif status_filter == "all":
            pass  # Include everything, including voided
        else:  # "active" - default: exclude voided
            invoices = invoices.exclude(status=InvoiceStatus.VOID)

        # Date filtering
        today = date.today()
        date_preset = self.request.GET.get("date_preset", "")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

        if date_preset == "ytd":
            invoices = invoices.filter(issue_date__gte=date(today.year, 1, 1))
        elif date_preset == "mtd":
            invoices = invoices.filter(issue_date__gte=date(today.year, today.month, 1))
        elif date_preset == "last_year":
            invoices = invoices.filter(
                issue_date__gte=date(today.year - 1, 1, 1),
                issue_date__lt=date(today.year, 1, 1)
            )
        elif date_from or date_to:
            if date_from:
                invoices = invoices.filter(issue_date__gte=date_from)
            if date_to:
                invoices = invoices.filter(issue_date__lte=date_to)

        invoices = invoices.order_by("-issue_date")

        # Pagination
        page_size = self.request.GET.get("per_page", DEFAULT_PAGE_SIZE)
        try:
            page_size = int(page_size)
            if page_size not in PAGE_SIZE_OPTIONS:
                page_size = DEFAULT_PAGE_SIZE
        except (ValueError, TypeError):
            page_size = DEFAULT_PAGE_SIZE

        paginator = Paginator(invoices, page_size)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context["invoices"] = page_obj
        context["page_obj"] = page_obj
        context["paginator"] = paginator
        context["status_filter"] = status_filter
        context["date_preset"] = date_preset
        context["date_from"] = date_from
        context["date_to"] = date_to
        context["per_page"] = page_size
        context["page_size_options"] = PAGE_SIZE_OPTIONS
        context["invoice_statuses"] = InvoiceStatus.choices

        # Financial summary data
        all_invoices = Invoice.objects.filter(client=client)
        outstanding_total = sum(inv.outstanding_balance() for inv in all_invoices)

        payments = Payment.objects.filter(client=client)
        unapplied_total = sum(p.unapplied_amount for p in payments)
        unapplied_count = sum(1 for p in payments if p.unapplied_amount > 0)

        net_position = outstanding_total - unapplied_total

        context["outstanding_total"] = outstanding_total
        context["unapplied_total"] = unapplied_total
        context["unapplied_count"] = unapplied_count
        context["net_position"] = net_position

        return context


class ClientUpdateView(LoginRequiredMixin, UpdateView):
    model = Client
    form_class = ClientForm
    template_name = "billing/client_form.html"
    success_url = reverse_lazy("billing:client_list")


@login_required
def client_unapplied_payments(request, pk):
    """HTMX endpoint for unapplied payments fragment."""
    client = get_object_or_404(Client, pk=pk)
    payments = Payment.objects.filter(client=client).order_by("-date")
    unapplied_payments = [p for p in payments if p.unapplied_amount > 0]

    return render(request, "billing/partials/unapplied_payments.html", {
        "client": client,
        "unapplied_payments": unapplied_payments,
    })
