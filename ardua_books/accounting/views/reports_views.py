from datetime import date, timedelta
from django.db.models import Sum, Case, When, F, DecimalField, Q
from django.views.generic import TemplateView

from accounting.models import ChartOfAccount, JournalLine, AccountType, Payment, PaymentApplication
from billing.models import Client, Invoice

class ReportsHomeView(TemplateView):
    template_name = "accounting/reports_home.html"

class TrialBalanceView(TemplateView):
    template_name = "accounting/trial_balance.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = date.today()

        # Get date filter parameters
        date_preset = self.request.GET.get("date_preset", "")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

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
        elif date_from or date_to:
            from_date = date.fromisoformat(date_from) if date_from else None
            to_date = date.fromisoformat(date_to) if date_to else None
            date_preset = ""
        else:
            from_date = None
            to_date = None

        # Build date filter for journal lines
        date_filter = Q()
        if from_date:
            date_filter &= Q(journalline__journal_entry__entry_date__gte=from_date)
        if to_date:
            date_filter &= Q(journalline__journal_entry__entry_date__lte=to_date)

        accounts = (
            ChartOfAccount.objects.all()
            .annotate(
                debit_sum=Sum(
                    Case(
                        When(date_filter & Q(journalline__debit__gt=0), then=F("journalline__debit")),
                        default=0,
                        output_field=DecimalField(),
                    )
                ),
                credit_sum=Sum(
                    Case(
                        When(date_filter & Q(journalline__credit__gt=0), then=F("journalline__credit")),
                        default=0,
                        output_field=DecimalField(),
                    )
                ),
            )
            .order_by("type", "code")
        )

        # Ending balance per account
        for acct in accounts:
            (acct.debit_sum, acct.credit_sum) = (
                acct.debit_sum or 0,
                acct.credit_sum or 0,
            )
            acct.balance = acct.debit_sum - acct.credit_sum

        total_debits = sum(a.debit_sum for a in accounts)
        total_credits = sum(a.credit_sum for a in accounts)

        context.update(
            {
                "accounts": accounts,
                "total_debits": total_debits,
                "total_credits": total_credits,
                "date_preset": date_preset,
                "date_from": date_from if not date_preset else "",
                "date_to": date_to if not date_preset else "",
                "from_date": from_date,
                "to_date": to_date,
            }
        )
        return context

class IncomeStatementView(TemplateView):
    template_name = "accounting/income_statement.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = date.today()

        # Get date filter parameters
        date_preset = self.request.GET.get("date_preset", "")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

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
        elif date_from or date_to:
            from_date = date.fromisoformat(date_from) if date_from else None
            to_date = date.fromisoformat(date_to) if date_to else None
            date_preset = ""
        else:
            from_date = None
            to_date = None

        # Build date filter for journal lines
        date_filter = Q()
        if from_date:
            date_filter &= Q(journalline__journal_entry__entry_date__gte=from_date)
        if to_date:
            date_filter &= Q(journalline__journal_entry__entry_date__lte=to_date)

        accounts = (
            ChartOfAccount.objects.filter(
                type__in=[AccountType.INCOME, AccountType.EXPENSE]
            )
            .annotate(
                debit_sum=Sum(
                    Case(
                        When(date_filter & Q(journalline__debit__gt=0), then=F("journalline__debit")),
                        default=0,
                        output_field=DecimalField(),
                    )
                ),
                credit_sum=Sum(
                    Case(
                        When(date_filter & Q(journalline__credit__gt=0), then=F("journalline__credit")),
                        default=0,
                        output_field=DecimalField(),
                    )
                ),
            )
            .order_by("type", "code")
        )

        # compute balances
        for a in accounts:
            a.balance = (a.debit_sum or 0) - (a.credit_sum or 0)

        # Separate revenue and expense accounts
        revenue_accounts = [a for a in accounts if a.type == AccountType.INCOME]
        expense_accounts = [a for a in accounts if a.type == AccountType.EXPENSE]

        revenue_total = sum(a.balance for a in revenue_accounts)
        expense_total = sum(a.balance for a in expense_accounts)

        net_income = revenue_total - expense_total

        context.update(
            {
                "revenue_accounts": revenue_accounts,
                "expense_accounts": expense_accounts,
                "revenue_total": revenue_total,
                "expense_total": expense_total,
                "net_income": net_income,
                "date_preset": date_preset,
                "date_from": date_from if not date_preset else "",
                "date_to": date_to if not date_preset else "",
                "from_date": from_date,
                "to_date": to_date,
            }
        )

        return context

class ClientBalanceSummaryView(TemplateView):
    template_name = "accounting/client_balance_summary.html"

    def get_context_data(self, **kwargs):
        from billing.models import Client, Invoice
        from accounting.models import Payment, PaymentApplication

        context = super().get_context_data(**kwargs)

        # -----------------------------
        # Build raw summary rows
        # -----------------------------
        rows = []
        for client in Client.objects.all().order_by("name"):
            invoices = Invoice.objects.filter(client=client)
            payments = Payment.objects.filter(client=client)

            total_invoiced = sum(inv.total for inv in invoices)
            outstanding = sum(inv.outstanding_balance() for inv in invoices)

            applied = sum(
                app.amount
                for app in PaymentApplication.objects.filter(invoice__client=client)
            )

            unapplied = sum(p.unapplied_amount for p in payments)

            rows.append({
                "client": client,
                "total_invoiced": total_invoiced,
                "applied": applied,
                "unapplied": unapplied,
                "outstanding": outstanding,
                "net_ar": outstanding - unapplied,
            })

        # -----------------------------
        # Handle sorting
        # -----------------------------
        sort_key = self.request.GET.get("sort", "name")

        def sort_name(row):
            return row["client"].name.lower()

        valid_sorts = {
            "name": sort_name,
            "total_invoiced": lambda r: r["total_invoiced"],
            "applied": lambda r: r["applied"],
            "unapplied": lambda r: r["unapplied"],
            "outstanding": lambda r: r["outstanding"],
            "net_ar": lambda r: r["net_ar"],
        }

        sort_func = valid_sorts.get(sort_key, sort_name)
        rows.sort(key=sort_func)

        context["summary"] = rows
        context["sort"] = sort_key

        return context

    
class ARAgingView(TemplateView):
    template_name = "accounting/ar_aging.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = date.today()

        # Get client filter
        client_filter = self.request.GET.get("client", "")

        # Build base queryset
        invoices = Invoice.objects.select_related("client").all()
        if client_filter:
            invoices = invoices.filter(client_id=client_filter)

        buckets = {
            "current": {"label": "Current (0-30 days)", "invoices": [], "total": 0},
            "31-60": {"label": "31-60 Days", "invoices": [], "total": 0},
            "61-90": {"label": "61-90 Days", "invoices": [], "total": 0},
            "over_90": {"label": "Over 90 Days", "invoices": [], "total": 0},
        }

        grand_total = 0
        for inv in invoices:
            bal = inv.outstanding_balance()
            if bal <= 0:
                continue
            age = (today - inv.due_date).days
            inv.outstanding = bal
            inv.age_days = age

            if age <= 30:
                buckets["current"]["invoices"].append(inv)
                buckets["current"]["total"] += bal
            elif age <= 60:
                buckets["31-60"]["invoices"].append(inv)
                buckets["31-60"]["total"] += bal
            elif age <= 90:
                buckets["61-90"]["invoices"].append(inv)
                buckets["61-90"]["total"] += bal
            else:
                buckets["over_90"]["invoices"].append(inv)
                buckets["over_90"]["total"] += bal
            grand_total += bal

        ctx["buckets"] = buckets
        ctx["grand_total"] = grand_total
        ctx["clients"] = Client.objects.all().order_by("name")
        ctx["client_filter"] = client_filter
        return ctx