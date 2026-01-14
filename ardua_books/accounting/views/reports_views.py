from datetime import date, timedelta
from decimal import Decimal
from django.db.models import Sum, Case, When, F, DecimalField, Q
from django.views.generic import TemplateView

from accounting.models import ChartOfAccount, JournalLine, AccountType, Payment, PaymentApplication, BankAccount, BankTransaction
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
            date_filter &= Q(journalline__entry__posted_at__date__gte=from_date)
        if to_date:
            date_filter &= Q(journalline__entry__posted_at__date__lte=to_date)

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

        # Get date filter parameters - default to YTD
        date_preset = self.request.GET.get("date_preset", "ytd")
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
        elif date_preset == "all":
            from_date = None
            to_date = None
        elif date_from or date_to:
            from_date = date.fromisoformat(date_from) if date_from else None
            to_date = date.fromisoformat(date_to) if date_to else None
            date_preset = ""
        else:
            # Default to YTD
            from_date = today.replace(month=1, day=1)
            to_date = today

        # Build date filter for journal lines
        date_filter = Q()
        if from_date:
            date_filter &= Q(journalline__entry__posted_at__date__gte=from_date)
        if to_date:
            date_filter &= Q(journalline__entry__posted_at__date__lte=to_date)

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
        # Revenue accounts have credit balances (credits > debits), so we negate to show positive
        # Expense accounts have debit balances (debits > credits), shown as positive
        for a in accounts:
            raw_balance = (a.debit_sum or 0) - (a.credit_sum or 0)
            if a.type == AccountType.INCOME:
                a.balance = -raw_balance  # Flip sign for revenue (credit balances)
            else:
                a.balance = raw_balance   # Expenses already positive (debit balances)

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


class AccountDrilldownView(TemplateView):
    """
    Drilldown view showing all journal entry lines for a specific account.
    Provides links to the source documents (Expense, Payment, Invoice, etc.)
    """
    template_name = "accounting/account_drilldown.html"

    def get_context_data(self, **kwargs):
        from django.shortcuts import get_object_or_404
        from accounting.models import JournalLine, JournalEntry
        from billing.models import Expense, Invoice
        from accounting.models import Payment, BankTransaction

        context = super().get_context_data(**kwargs)

        account = get_object_or_404(ChartOfAccount, pk=self.kwargs["pk"])
        today = date.today()

        # Get date filter parameters from query string
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

        # Parse dates
        from_date = date.fromisoformat(date_from) if date_from else None
        to_date = date.fromisoformat(date_to) if date_to else None

        # Query journal lines for this account
        lines = JournalLine.objects.filter(account=account).select_related(
            "entry", "entry__source_content_type"
        ).order_by("entry__posted_at", "entry__id")

        if from_date:
            lines = lines.filter(entry__posted_at__date__gte=from_date)
        if to_date:
            lines = lines.filter(entry__posted_at__date__lte=to_date)

        # Enrich each line with source document info
        enriched_lines = []
        for line in lines:
            je = line.entry
            source_url = None
            source_label = None

            # Try to get the source document URL
            if je.source_content_type and je.source_object_id:
                model_class = je.source_content_type.model_class()
                model_name = je.source_content_type.model

                try:
                    if model_name == "expense":
                        source_url = f"/billing/expenses/{je.source_object_id}/"
                        source_label = "Expense"
                    elif model_name == "payment":
                        source_url = f"/accounting/payments/{je.source_object_id}/"
                        source_label = "Payment"
                    elif model_name == "invoice":
                        source_url = f"/billing/invoices/{je.source_object_id}/"
                        source_label = "Invoice"
                    elif model_name == "banktransaction":
                        # For bank transactions, link to the register
                        try:
                            txn = BankTransaction.objects.get(pk=je.source_object_id)
                            source_url = f"/accounting/bank-accounts/{txn.bank_account_id}/register/"
                            source_label = "Bank Txn"
                        except BankTransaction.DoesNotExist:
                            pass
                    elif model_name == "bankaccount":
                        source_url = f"/accounting/bank-accounts/"
                        source_label = "Bank Account"
                except Exception:
                    pass

            enriched_lines.append({
                "line": line,
                "je": je,
                "source_url": source_url,
                "source_label": source_label,
            })

        context.update({
            "account": account,
            "lines": enriched_lines,
            "from_date": from_date,
            "to_date": to_date,
            "date_from": date_from,
            "date_to": date_to,
        })

        return context


class BankReconciliationScheduleView(TemplateView):
    """
    Bank Reconciliation Schedule Report showing all bank accounts with
    opening balances, activity, ending balances, and unmatched transactions.
    """
    template_name = "accounting/bank_reconciliation_schedule.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = date.today()

        # Get date filter parameters - default to last year
        date_preset = self.request.GET.get("date_preset", "last_year")
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
            # Default to last year
            from_date = date(today.year - 1, 1, 1)
            to_date = date(today.year - 1, 12, 31)

        # Build report data for each bank account
        accounts_data = []
        total_bank_assets = Decimal("0")
        total_credit_cards = Decimal("0")

        for bank_account in BankAccount.objects.select_related("account").order_by("type", "institution"):
            # Get all transactions for this account
            all_transactions = BankTransaction.objects.filter(bank_account=bank_account)

            # Opening balance is the account's opening_balance plus all transactions BEFORE the from_date
            opening_balance = bank_account.opening_balance or Decimal("0")
            if from_date:
                prior_txn_sum = all_transactions.filter(
                    date__lt=from_date
                ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
                opening_balance += prior_txn_sum

            # Get transactions within the date range
            period_transactions = all_transactions
            if from_date:
                period_transactions = period_transactions.filter(date__gte=from_date)
            if to_date:
                period_transactions = period_transactions.filter(date__lte=to_date)

            # Calculate deposits and withdrawals within period
            deposits = period_transactions.filter(amount__gt=0).aggregate(s=Sum("amount"))["s"] or Decimal("0")
            withdrawals = period_transactions.filter(amount__lt=0).aggregate(s=Sum("amount"))["s"] or Decimal("0")

            # Ending balance
            ending_balance = opening_balance + deposits + withdrawals

            # Get unmatched transactions within the period
            unmatched_transactions = period_transactions.filter(
                payment__isnull=True,
                expense__isnull=True,
                transfer_pair__isnull=True,
            ).order_by("date")

            unmatched_count = unmatched_transactions.count()
            unmatched_total = unmatched_transactions.aggregate(s=Sum("amount"))["s"] or Decimal("0")

            account_data = {
                "bank_account": bank_account,
                "gl_account": bank_account.account,
                "opening_balance": opening_balance,
                "deposits": deposits,
                "withdrawals": withdrawals,
                "ending_balance": ending_balance,
                "unmatched_transactions": list(unmatched_transactions[:10]),  # Limit for display
                "unmatched_count": unmatched_count,
                "unmatched_total": unmatched_total,
                "has_more_unmatched": unmatched_count > 10,
            }
            accounts_data.append(account_data)

            # Accumulate totals by account type
            if bank_account.type in ["CHECKING", "SAVINGS", "CASH"]:
                total_bank_assets += ending_balance
            elif bank_account.type == "CREDIT_CARD":
                total_credit_cards += ending_balance  # Credit cards typically negative

        net_cash_position = total_bank_assets + total_credit_cards

        context.update({
            "accounts_data": accounts_data,
            "total_bank_assets": total_bank_assets,
            "total_credit_cards": total_credit_cards,
            "net_cash_position": net_cash_position,
            "date_preset": date_preset,
            "date_from": date_from if not date_preset else "",
            "date_to": date_to if not date_preset else "",
            "from_date": from_date,
            "to_date": to_date,
        })

        return context