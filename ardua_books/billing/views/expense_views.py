"""
Expense management views.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DetailView, DeleteView

from billing.models import Expense
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
    success_url = reverse_lazy("billing:expense_list")

    def form_valid(self, form):
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
