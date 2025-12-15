"""
Time entry management views.
"""
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, TemplateView

from billing.models import Client, Consultant, TimeEntry, BillableStatus
from billing.forms import TimeEntryForm


class TimeEntryListView(LoginRequiredMixin, TemplateView):
    template_name = "billing/timeentry_list.html"

    DEFAULT_PAGE_SIZE = 25
    PAGE_SIZE_OPTIONS = [10, 25, 50, 100]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = date.today()

        # Get filter parameters
        client_filter = self.request.GET.get("client", "")
        consultant_filter = self.request.GET.get("consultant", "")
        status_filter = self.request.GET.get("status", "")
        date_preset = self.request.GET.get("date_preset", "")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

        # Build queryset
        qs = TimeEntry.objects.select_related("client", "consultant").order_by("-work_date", "-created_at")

        # Apply filters
        if client_filter:
            qs = qs.filter(client_id=client_filter)
        if consultant_filter:
            qs = qs.filter(consultant_id=consultant_filter)
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
            qs = qs.filter(work_date__gte=from_date)
        if to_date:
            qs = qs.filter(work_date__lte=to_date)

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
            "time_entries": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "clients": Client.objects.all().order_by("name"),
            "consultants": Consultant.objects.all().order_by("display_name"),
            "client_filter": client_filter,
            "consultant_filter": consultant_filter,
            "status_choices": BillableStatus.choices,
            "status_filter": status_filter,
            "date_preset": date_preset,
            "date_from": date_from if not date_preset else "",
            "date_to": date_to if not date_preset else "",
            "per_page": page_size,
            "page_size_options": self.PAGE_SIZE_OPTIONS,
        })
        return ctx


class TimeEntryCreateView(LoginRequiredMixin, CreateView):
    model = TimeEntry
    form_class = TimeEntryForm
    template_name = "billing/timeentry_form.html"

    def get_success_url(self):
        # Stay on the create page with client/consultant pre-filled
        url = reverse("billing:timeentry_create")
        params = []
        if self.object.client_id:
            params.append(f"client={self.object.client_id}")
        if self.object.consultant_id:
            params.append(f"consultant={self.object.consultant_id}")
        if params:
            url += "?" + "&".join(params)
        return url

    def get_initial(self):
        initial = super().get_initial()
        # Pre-fill from query params (after successful submit)
        client_id = self.request.GET.get("client")
        consultant_id = self.request.GET.get("consultant")
        if client_id:
            initial["client"] = client_id
        if consultant_id:
            initial["consultant"] = consultant_id
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Get client_id from form or query param
        client_id = self.request.GET.get("client")
        if client_id:
            context["selected_client_id"] = client_id
            context["recent_entries"] = TimeEntry.objects.filter(
                client_id=client_id
            ).select_related("client", "consultant").order_by("-work_date", "-created_at")[:20]
        else:
            context["recent_entries"] = []
        return context

    def form_valid(self, form):
        billing_rate = form.cleaned_data.get("billing_rate")
        client = form.cleaned_data.get("client")

        if (billing_rate is None or billing_rate == 0) and client and client.default_hourly_rate is not None:
            form.instance.billing_rate = client.default_hourly_rate

        response = super().form_valid(form)
        messages.success(self.request, "Time entry saved.")
        return response


@login_required
def timeentry_client_entries(request, client_id):
    """HTMX endpoint for time entries by client."""
    client = get_object_or_404(Client, pk=client_id)
    entries = TimeEntry.objects.filter(client=client).select_related(
        "client", "consultant"
    ).order_by("-work_date", "-created_at")[:20]

    return render(request, "billing/partials/timeentry_list.html", {
        "entries": entries,
        "client": client,
    })


class TimeEntryUpdateView(LoginRequiredMixin, UpdateView):
    model = TimeEntry
    form_class = TimeEntryForm
    template_name = "billing/timeentry_form.html"
    success_url = reverse_lazy("billing:timeentry_list")

    def get_queryset(self):
        # Only allow editing non-billed entries
        qs = super().get_queryset()
        return qs.exclude(status=BillableStatus.BILLED).select_related("client", "consultant")
