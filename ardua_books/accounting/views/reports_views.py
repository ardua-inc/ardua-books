from datetime import date
from django.db.models import Sum, Case, When, F, DecimalField
from django.views.generic import TemplateView

from accounting.models import ChartOfAccount, JournalLine, AccountType, Payment, PaymentApplication
from billing.models import Client, Invoice

class ReportsHomeView(TemplateView):
    template_name = "accounting/reports_home.html"

class TrialBalanceView(TemplateView):
    template_name = "accounting/trial_balance.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        accounts = (
            ChartOfAccount.objects.all()
            .annotate(
                debit_sum=Sum(
                    Case(
                        When(journalline__debit__gt=0, then=F("journalline__debit")),
                        default=0,
                        output_field=DecimalField(),
                    )
                ),
                credit_sum=Sum(
                    Case(
                        When(journalline__credit__gt=0, then=F("journalline__credit")),
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
            }
        )
        return context

class IncomeStatementView(TemplateView):
    template_name = "accounting/income_statement.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        accounts = (
            ChartOfAccount.objects.filter(
                type__in=[AccountType.INCOME, AccountType.EXPENSE]
            )
            .annotate(
                debit_sum=Sum(
                    Case(
                        When(journalline__debit__gt=0, then=F("journalline__debit")),
                        default=0,
                        output_field=DecimalField(),
                    )
                ),
                credit_sum=Sum(
                    Case(
                        When(journalline__credit__gt=0, then=F("journalline__credit")),
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

        revenue_total = sum(
            a.balance for a in accounts if a.type == AccountType.INCOME
        )
        expense_total = sum(
            a.balance for a in accounts if a.type == AccountType.EXPENSE
        )

        net_income = revenue_total - expense_total

        context.update(
            {
                "accounts": accounts,
                "revenue_total": revenue_total,
                "expense_total": expense_total,
                "net_income": net_income,
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
        buckets = {
            "0-30": [],
            "31-60": [],
            "61-90": [],
            "90+": []
        }
        for inv in Invoice.objects.all():
            bal = inv.outstanding_balance()
            if bal <= 0:
                continue
            age = (today - inv.due_date).days
            if age <= 30:
                buckets["0-30"].append(inv)
            elif age <= 60:
                buckets["31-60"].append(inv)
            elif age <= 90:
                buckets["61-90"].append(inv)
            else:
                buckets["90+"].append(inv)
        ctx["buckets"] = buckets
        return ctx