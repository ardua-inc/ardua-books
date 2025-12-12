"""
General Ledger / Journal Entry views.
"""
from django.views.generic import ListView, DetailView

from accounting.models import JournalEntry, JournalLine


class JournalEntryListView(ListView):
    model = JournalEntry
    template_name = "accounting/journal_entry_list.html"
    paginate_by = 50
    ordering = ["-posted_at", "-id"]


class JournalEntryDetailView(DetailView):
    model = JournalEntry
    template_name = "accounting/journal_entry_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entry = self.object

        context["lines"] = JournalLine.objects.filter(entry=entry).select_related("account")

        if entry.source_object:
            context["source_object"] = entry.source_object

        return context
