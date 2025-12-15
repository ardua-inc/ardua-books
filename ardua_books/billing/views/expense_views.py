"""
Expense management views.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DetailView, DeleteView

from billing.models import Client, Expense
from billing.forms import ExpenseForm


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

    def get_success_url(self):
        # Stay on the create page with client pre-filled
        url = reverse("billing:expense_create")
        if self.object.client_id:
            url += f"?client={self.object.client_id}"
        return url

    def get_initial(self):
        initial = super().get_initial()
        # Pre-fill from query params (after successful submit)
        client_id = self.request.GET.get("client")
        if client_id:
            initial["client"] = client_id
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Get client_id from form or query param
        client_id = self.request.GET.get("client")
        if client_id:
            context["selected_client_id"] = client_id
            context["recent_expenses"] = Expense.objects.filter(
                client_id=client_id
            ).select_related("client", "category").order_by("-expense_date", "-created_at")[:20]
        else:
            context["recent_expenses"] = []
        return context

    def form_valid(self, form):
        if form.cleaned_data.get("billable") and not form.cleaned_data.get("client"):
            form.instance.billable = False
        response = super().form_valid(form)
        messages.success(self.request, "Expense saved.")
        return response


@login_required
def expense_client_entries(request, client_id):
    """HTMX endpoint for expenses by client."""
    client = get_object_or_404(Client, pk=client_id)
    expenses = Expense.objects.filter(client=client).select_related(
        "client", "category"
    ).order_by("-expense_date", "-created_at")[:20]

    return render(request, "billing/partials/expense_list.html", {
        "expenses": expenses,
        "client": client,
    })


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
