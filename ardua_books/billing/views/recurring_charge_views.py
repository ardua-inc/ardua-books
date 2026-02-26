"""
Views for managing recurring charges.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView

from billing.models import RecurringCharge
from billing.forms import RecurringChargeForm
from accounting.views.mixins import ReadOnlyUserMixin


class RecurringChargeListView(LoginRequiredMixin, ListView):
    model = RecurringCharge
    template_name = "billing/recurring_charge_list.html"
    context_object_name = "recurring_charges"

    def get_queryset(self):
        qs = RecurringCharge.objects.select_related("client").order_by(
            "client__name", "description"
        )
        client_id = self.request.GET.get("client")
        if client_id:
            qs = qs.filter(client_id=client_id)
        active = self.request.GET.get("active")
        if active == "1":
            qs = qs.filter(is_active=True)
        elif active == "0":
            qs = qs.filter(is_active=False)
        return qs

    def get_context_data(self, **kwargs):
        from billing.models import Client
        ctx = super().get_context_data(**kwargs)
        ctx["clients"] = Client.objects.filter(is_active=True).order_by("name")
        ctx["client_filter"] = self.request.GET.get("client", "")
        ctx["active_filter"] = self.request.GET.get("active", "")
        return ctx


class RecurringChargeCreateView(ReadOnlyUserMixin, LoginRequiredMixin, CreateView):
    model = RecurringCharge
    form_class = RecurringChargeForm
    template_name = "billing/recurring_charge_form.html"
    success_url = reverse_lazy("billing:recurring_charge_list")

    def get_initial(self):
        initial = super().get_initial()
        client_id = self.request.GET.get("client")
        if client_id:
            initial["client"] = client_id
        return initial


class RecurringChargeUpdateView(ReadOnlyUserMixin, LoginRequiredMixin, UpdateView):
    model = RecurringCharge
    form_class = RecurringChargeForm
    template_name = "billing/recurring_charge_form.html"
    success_url = reverse_lazy("billing:recurring_charge_list")


class RecurringChargeDeleteView(ReadOnlyUserMixin, LoginRequiredMixin, DeleteView):
    model = RecurringCharge
    template_name = "billing/recurring_charge_confirm_delete.html"
    success_url = reverse_lazy("billing:recurring_charge_list")

    def get_queryset(self):
        # Only allow deleting charges that have never been billed
        return RecurringCharge.objects.filter(occurrences__isnull=True)
