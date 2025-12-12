"""
Client management views.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DetailView

from billing.models import Client, Invoice
from billing.forms import ClientForm
from accounting.models import Payment


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
        invoices = Invoice.objects.filter(client=client).order_by("-issue_date")
        context["invoices"] = invoices
        return context


class ClientUpdateView(LoginRequiredMixin, UpdateView):
    model = Client
    form_class = ClientForm
    template_name = "billing/client_form.html"
    success_url = reverse_lazy("billing:client_list")


class ClientFinancialView(LoginRequiredMixin, DetailView):
    model = Client
    template_name = "billing/client_financial.html"
    context_object_name = "client"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        client = self.object

        invoices = Invoice.objects.filter(client=client).order_by("due_date")
        outstanding_total = sum(inv.outstanding_balance() for inv in invoices)

        payments = Payment.objects.filter(client=client).order_by("-date")
        unapplied_total = sum(p.unapplied_amount for p in payments)

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

        unapplied_payments = [p for p in payments if p.unapplied_amount > 0]
        net_position = outstanding_total - unapplied_total

        context.update({
            "outstanding_total": outstanding_total,
            "unapplied_total": unapplied_total,
            "net_position": net_position,
            "outstanding_invoices": outstanding_invoices,
            "unapplied_payments": unapplied_payments,
        })

        return context
