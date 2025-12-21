"""
Expense management views.
"""
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DetailView, DeleteView, TemplateView

from billing.models import Client, Expense, ExpenseCategory, BillableStatus
from billing.forms import ExpenseForm
from accounting.views.mixins import FilterPersistenceMixin, ReadOnlyUserMixin


class ExpenseListView(FilterPersistenceMixin, LoginRequiredMixin, TemplateView):
    template_name = "billing/expense_list.html"

    # Filter persistence
    filter_persistence_key = "expense_list_filters"
    filter_params = ["client", "category", "status", "billable", "date_preset", "date_from", "date_to", "per_page"]

    DEFAULT_PAGE_SIZE = 25
    PAGE_SIZE_OPTIONS = [10, 25, 50, 100]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = date.today()

        # Get filter parameters
        client_filter = self.request.GET.get("client", "")
        category_filter = self.request.GET.get("category", "")
        status_filter = self.request.GET.get("status", "")
        billable_filter = self.request.GET.get("billable", "")
        date_preset = self.request.GET.get("date_preset", "")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

        # Build queryset
        qs = Expense.objects.select_related("client", "category").order_by("-expense_date", "-created_at")

        # Apply filters
        if client_filter:
            qs = qs.filter(client_id=client_filter)
        if category_filter:
            qs = qs.filter(category_id=category_filter)
        if status_filter:
            qs = qs.filter(status=status_filter)
        if billable_filter:
            qs = qs.filter(billable=(billable_filter == "yes"))

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
            qs = qs.filter(expense_date__gte=from_date)
        if to_date:
            qs = qs.filter(expense_date__lte=to_date)

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
            "expenses": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "clients": Client.objects.all().order_by("name"),
            "categories": ExpenseCategory.objects.all().order_by("name"),
            "client_filter": client_filter,
            "category_filter": category_filter,
            "status_choices": BillableStatus.choices,
            "status_filter": status_filter,
            "billable_filter": billable_filter,
            "date_preset": date_preset,
            "date_from": date_from if not date_preset else "",
            "date_to": date_to if not date_preset else "",
            "per_page": page_size,
            "page_size_options": self.PAGE_SIZE_OPTIONS,
        })
        return ctx


class ExpenseCreateView(ReadOnlyUserMixin, LoginRequiredMixin, CreateView):
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


class ExpenseUpdateView(ReadOnlyUserMixin, LoginRequiredMixin, UpdateView):
    model = Expense
    form_class = ExpenseForm
    template_name = "billing/expense_form.html"
    success_url = reverse_lazy("billing:expense_list")

    def get_queryset(self):
        # Only allow editing non-billed expenses
        qs = super().get_queryset()
        return qs.exclude(status=BillableStatus.BILLED)

    def form_valid(self, form):
        if form.cleaned_data.get("billable") and not form.cleaned_data.get("client"):
            form.instance.billable = False
        return super().form_valid(form)


class ExpenseDeleteView(ReadOnlyUserMixin, LoginRequiredMixin, DeleteView):
    model = Expense
    template_name = "billing/expense_confirm_delete.html"
    success_url = reverse_lazy("billing:expense_list")
