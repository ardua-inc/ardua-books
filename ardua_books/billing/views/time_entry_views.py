"""
Time entry management views.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView

from billing.models import TimeEntry
from billing.forms import TimeEntryForm


class TimeEntryListView(LoginRequiredMixin, ListView):
    model = TimeEntry
    template_name = "billing/timeentry_list.html"
    context_object_name = "time_entries"

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.select_related("client", "consultant").order_by("-work_date", "-created_at")


class TimeEntryCreateView(LoginRequiredMixin, CreateView):
    model = TimeEntry
    form_class = TimeEntryForm
    template_name = "billing/timeentry_form.html"
    success_url = reverse_lazy("billing:timeentry_list")

    def form_valid(self, form):
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
        qs = super().get_queryset()
        return qs.select_related("client", "consultant")
