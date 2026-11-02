"""
General Ledger / Journal Entry views.
"""
from datetime import date, timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView, DetailView, TemplateView

from accounting.forms import ManualJournalEntryForm, ManualJournalLineFormSet, ReverseEntryForm
from accounting.models import JournalEntry, JournalLine
from accounting.services.journal import JournalEntryService
from accounting.views.mixins import FilterPersistenceMixin, ReadOnlyUserMixin


class JournalEntryListView(LoginRequiredMixin, FilterPersistenceMixin, TemplateView):
    template_name = "accounting/journal_entry_list.html"

    # Filter persistence
    filter_persistence_key = "journal_list_filters"
    filter_params = ["date_preset", "date_from", "date_to", "per_page"]

    DEFAULT_PAGE_SIZE = 25
    PAGE_SIZE_OPTIONS = [10, 25, 50, 100]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = date.today()

        # Get filter parameters
        date_preset = self.request.GET.get("date_preset", "")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

        # Build queryset
        qs = JournalEntry.objects.order_by("-posted_at", "-id")

        # Determine date range
        if date_preset == "mtd":
            from_date = today.replace(day=1)
            to_date = today
        elif date_preset == "ytd":
            from_date = today.replace(month=1, day=1)
            to_date = today
        elif date_preset == "last_month":
            first_of_month = today.replace(day=1)
            last_month_end = first_of_month - timedelta(days=1)
            from_date = last_month_end.replace(day=1)
            to_date = last_month_end
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
            qs = qs.filter(posted_at__date__gte=from_date)
        if to_date:
            qs = qs.filter(posted_at__date__lte=to_date)

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
            "entries": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "date_preset": date_preset,
            "date_from": date_from if not date_preset else "",
            "date_to": date_to if not date_preset else "",
            "per_page": page_size,
            "page_size_options": self.PAGE_SIZE_OPTIONS,
        })
        return ctx


class JournalEntryDetailView(LoginRequiredMixin, DetailView):
    model = JournalEntry
    template_name = "accounting/journal_entry_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entry = self.object

        context["lines"] = JournalLine.objects.filter(entry=entry).select_related("account")

        if entry.source_object:
            context["source_object"] = entry.source_object

        context["reverses"] = entry.reverses
        context["reversed_by"] = entry.reversed_by
        context["can_reverse"] = entry.is_manual and not context["reverses"] and not context["reversed_by"]
        if context["can_reverse"]:
            context["reverse_form"] = ReverseEntryForm()
        return context


class ManualJournalEntryCreateView(LoginRequiredMixin, ReadOnlyUserMixin, View):
    """Enter a balanced journal entry by hand (JournalEntryService.create_manual_entry)."""
    template_name = "accounting/journal_entry_form.html"

    def get(self, request):
        return render(request, self.template_name, {
            "form": ManualJournalEntryForm(),
            "formset": ManualJournalLineFormSet(prefix="lines"),
        })

    def post(self, request):
        form = ManualJournalEntryForm(request.POST)
        formset = ManualJournalLineFormSet(request.POST, prefix="lines")
        context = {"form": form, "formset": formset}
        if not (form.is_valid() and formset.is_valid()):
            return render(request, self.template_name, context)
        try:
            je = JournalEntryService.create_manual_entry(
                form.cleaned_data["date"], form.cleaned_data["memo"], formset.lines(), user=request.user,
            )
        except ValueError as e:
            context["entry_error"] = str(e)
            return render(request, self.template_name, context)
        messages.success(request, f"Journal entry #{je.id} posted.")
        return redirect("accounting:journal_detail", pk=je.id)


class JournalEntryReverseView(LoginRequiredMixin, ReadOnlyUserMixin, View):
    """Post the reversal of a manual entry (from its detail page)."""

    def post(self, request, pk):
        je = get_object_or_404(JournalEntry, pk=pk)
        form = ReverseEntryForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Enter a valid reversal date.")
            return redirect("accounting:journal_detail", pk=je.id)
        try:
            reversal = JournalEntryService.reverse_entry(je, form.cleaned_data["date"], user=request.user)
        except ValueError as e:
            messages.error(request, f"Could not reverse: {e}")
            return redirect("accounting:journal_detail", pk=je.id)
        messages.success(request, f"JE #{je.id} reversed by JE #{reversal.id}.")
        return redirect("accounting:journal_detail", pk=reversal.id)
