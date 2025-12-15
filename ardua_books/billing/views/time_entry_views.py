"""
Time entry management views.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView

from billing.models import Client, TimeEntry
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
        qs = super().get_queryset()
        return qs.select_related("client", "consultant")
