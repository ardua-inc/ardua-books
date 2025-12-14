"""
Bank account and transaction management views.
"""
import csv
from datetime import date, timedelta, datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import ListView, DetailView, FormView, TemplateView

from accounting.forms import (
    BankTransactionForm,
    BankAccountForm,
    CSVImportForm,
    LinkPaymentToTransactionForm,
    BankTransactionLinkExpenseForm,
)
from accounting.models import (
    ChartOfAccount,
    AccountType,
    BankAccount,
    BankTransaction,
    BankImportProfile,
)
from accounting.services.banking import BankAccountService, BankTransactionService
from accounting.services.importing import normalize_amount


class BankAccountListView(ListView):
    model = BankAccount
    template_name = "accounting/bankaccount_list.html"
    context_object_name = "accounts"


class BankAccountCreateView(FormView):
    template_name = "accounting/bankaccount_form.html"
    form_class = BankAccountForm
    success_url = reverse_lazy("accounting:bankaccount_list")

    def form_valid(self, form):
        BankAccountService.create_bank_account(
            type=form.cleaned_data["type"],
            institution=form.cleaned_data["institution"],
            masked=form.cleaned_data["account_number_masked"],
            opening_balance=form.cleaned_data["opening_balance"],
        )
        return super().form_valid(form)


class BankAccountDetailView(DetailView):
    model = BankAccount
    template_name = "accounting/bankaccount_detail.html"
    context_object_name = "account"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["recent_transactions"] = self.object.transactions.all()[:20]
        return ctx


class BankTransactionCreateView(FormView):
    template_name = "accounting/banktransaction_form.html"
    form_class = BankTransactionForm

    def dispatch(self, request, *args, **kwargs):
        self.bank_account = get_object_or_404(BankAccount, pk=kwargs["account_id"])
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)

        if self.request.method == "POST":
            amount = self.request.POST.get("amount")
            try:
                amt = Decimal(amount)
            except Exception:
                amt = None

            form.cleaned_data = {}
            form.cleaned_data["amount"] = amt
            form.filter_accounts(bank_account=self.bank_account)

        return form

    def form_valid(self, form):
        BankTransactionService.post_transaction(
            bank_account=self.bank_account,
            date=form.cleaned_data["date"],
            description=form.cleaned_data["description"],
            amount=form.cleaned_data["amount"],
            offset_account=form.cleaned_data["offset_account"],
        )
        messages.success(self.request, "Transaction posted.")
        return redirect("accounting:bankaccount_detail", pk=self.bank_account.pk)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["bank_account"] = self.bank_account
        return ctx


class BankTransactionListView(ListView):
    model = BankTransaction
    template_name = "accounting/banktransaction_list.html"
    context_object_name = "transactions"


class BankTransactionListForAccountView(ListView):
    model = BankTransaction
    template_name = "accounting/banktransaction_list.html"
    context_object_name = "transactions"

    def get_queryset(self):
        self.bank_account = get_object_or_404(BankAccount, pk=self.kwargs["account_id"])
        return BankTransaction.objects.filter(bank_account=self.bank_account)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["bank_account"] = self.bank_account
        return ctx


class OffsetAccountFilterView(View):
    """AJAX view to filter offset accounts based on transaction amount."""

    def get(self, request, *args, **kwargs):
        amount_raw = request.GET.get("amount")
        bank_account_id = request.GET.get("bank_account")

        qs = ChartOfAccount.objects.none()

        try:
            amount = Decimal(amount_raw)
        except Exception:
            amount = None

        excluded_ids = []
        if bank_account_id:
            try:
                excluded_ids.append(int(bank_account_id))
            except ValueError:
                pass

        if amount is not None:
            if amount > 0:
                qs = ChartOfAccount.objects.filter(
                    type__in=[
                        AccountType.INCOME,
                        AccountType.LIABILITY,
                        AccountType.EQUITY,
                    ]
                )
            elif amount < 0:
                qs = ChartOfAccount.objects.filter(
                    type__in=[
                        AccountType.EXPENSE,
                        AccountType.LIABILITY,
                        AccountType.EQUITY,
                    ]
                )

            qs = qs.exclude(id__in=excluded_ids).order_by("type", "code")

        html = render_to_string(
            "accounting/partials/offset_account_select.html",
            {"accounts": qs},
        )

        return HttpResponse(html)


class BankRegisterView(TemplateView):
    template_name = "accounting/bank_register.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        bank_account = get_object_or_404(BankAccount, pk=self.kwargs["pk"])
        today = date.today()

        range_code = self.request.GET.get("range")
        from_str = self.request.GET.get("from")
        to_str = self.request.GET.get("to")

        if not range_code and not from_str and not to_str:
            range_code = "last30"

        if range_code == "last30":
            from_date = today - timedelta(days=30)
            to_date = today
        elif range_code == "last90":
            from_date = today - timedelta(days=90)
            to_date = today
        elif range_code == "month":
            from_date = today.replace(day=1)
            to_date = today
        elif range_code == "ytd":
            from_date = today.replace(month=1, day=1)
            to_date = today
        elif range_code == "all":
            from_date = None
            to_date = None
        else:
            from_date = date.fromisoformat(from_str) if from_str else None
            to_date = date.fromisoformat(to_str) if to_str else None

        tx_qs = BankTransaction.objects.filter(bank_account=bank_account)

        if from_date:
            tx_qs = tx_qs.filter(date__gte=from_date)
        if to_date:
            tx_qs = tx_qs.filter(date__lte=to_date)

        tx_qs = tx_qs.order_by("date", "id")
        transactions = list(tx_qs)

        if from_date:
            earlier_sum = (
                BankTransaction.objects.filter(bank_account=bank_account, date__lt=from_date)
                .aggregate(total=Sum("amount"))
                .get("total") or Decimal("0.00")
            )
        else:
            earlier_sum = Decimal("0.00")

        opening_balance = bank_account.opening_balance or Decimal("0.00")
        balance_forward = opening_balance + earlier_sum

        running = balance_forward
        for txn in transactions:
            running += txn.amount
            txn.running_balance = running

        ctx.update({
            "bank_account": bank_account,
            "transactions": transactions,
            "opening_balance": opening_balance,
            "balance_forward": balance_forward,
            "from_date": from_date,
            "to_date": to_date,
            "range_code": range_code,
        })
        return ctx


class BankTransactionDetailView(DetailView):
    model = BankTransaction
    template_name = "accounting/banktransaction_detail.html"
    context_object_name = "txn"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        txn = ctx["txn"]

        if txn.amount < 0:
            ctx["withdrawal"] = abs(txn.amount)
            ctx["deposit"] = None
        else:
            ctx["withdrawal"] = None
            ctx["deposit"] = txn.amount

        return ctx


class BankTransactionCSVImportView(View):
    template_name = "accounting/banktransaction_import.html"

    def get(self, request, pk):
        account = get_object_or_404(BankAccount, pk=pk)
        form = CSVImportForm()
        return render(request, self.template_name, {"form": form, "account": account})

    @transaction.atomic
    def post(self, request, pk):
        account = get_object_or_404(BankAccount, pk=pk)
        form = CSVImportForm(request.POST, request.FILES)

        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "account": account})

        csv_file = form.cleaned_data["file"]
        offset_account = form.cleaned_data["offset_account"]

        try:
            profile = account.import_profile
        except BankImportProfile.DoesNotExist:
            messages.error(request, "No import profile defined for this account.")
            return redirect("accounting:bankaccount_detail", pk=account.pk)

        decoded = csv_file.read().decode("utf-8").splitlines()
        reader = csv.reader(decoded)

        count = 0

        for row in reader:
            if not row:
                continue

            if not row[profile.date_column_index].strip()[0].isdigit():
                continue

            raw_date = row[profile.date_column_index].strip()
            raw_desc = row[profile.description_column_index].strip()
            raw_amount = row[profile.amount_column_index].strip()

            if profile.skip_if_description_contains:
                if profile.skip_if_description_contains.lower() in raw_desc.lower():
                    continue

            dt = datetime.strptime(raw_date, profile.date_format).date()
            amt = Decimal(raw_amount)
            amt = normalize_amount(amt, profile)

            BankTransactionService.post_transaction(
                bank_account=account,
                date=dt,
                description=raw_desc,
                amount=amt,
                offset_account=offset_account,
            )

            count += 1

        messages.success(request, f"Imported {count} transactions.")
        return redirect("accounting:bankaccount_detail", pk=account.pk)


class BankTransactionLinkPaymentView(View):
    template_name = "accounting/banktxn_link_payment.html"

    def get(self, request, txn_id):
        txn = get_object_or_404(BankTransaction, pk=txn_id)

        if txn.payment:
            messages.error(request, "This transaction is already linked to a payment.")
            return redirect("accounting:bankaccount_register", pk=txn.bank_account_id)

        form = LinkPaymentToTransactionForm(txn=txn)
        return render(request, self.template_name, {"form": form, "txn": txn})

    def post(self, request, txn_id):
        txn = get_object_or_404(BankTransaction, pk=txn_id)
        form = LinkPaymentToTransactionForm(request.POST, txn=txn)

        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "txn": txn})

        payment = form.cleaned_data["payment"]

        try:
            BankTransactionService.link_existing_payment(txn=txn, payment=payment)
        except Exception as e:
            messages.error(request, f"Could not link payment: {e}")
            return render(request, self.template_name, {"form": form, "txn": txn})

        messages.success(request, "Transaction linked to payment.")
        return redirect("accounting:bankaccount_register", pk=txn.bank_account_id)


class BankTransactionMarkOwnerEquityView(View):
    template_name = "accounting/banktxn_mark_owner_equity.html"

    def get(self, request, txn_id):
        txn = get_object_or_404(BankTransaction, pk=txn_id)
        return render(request, self.template_name, {"txn": txn})

    def post(self, request, txn_id):
        txn = get_object_or_404(BankTransaction, pk=txn_id)

        try:
            BankTransactionService.mark_as_owner_equity(txn=txn)
        except Exception as e:
            messages.error(request, f"Could not mark as equity: {e}")
            return redirect("accounting:bankaccount_register", pk=txn.bank_account_id)

        messages.success(request, "Transaction recorded as Owner Equity.")
        return redirect("accounting:bankaccount_register", pk=txn.bank_account_id)

@login_required
def banktransaction_link_expense(request, pk):
    txn = get_object_or_404(BankTransaction, pk=pk)

    # Hard guardrail
    if txn.payment_id:
        messages.error(request, "This transaction is already linked to a payment.")
        return redirect("accounting:bankaccount_register", txn.bank_account.id)

    if txn.expense_id:
        messages.error(request, "This transaction is already linked to an expense.")
        return redirect("accounting:bankaccount_register", txn.bank_account.id)

    if request.method == "POST":
        form = BankTransactionLinkExpenseForm(
            request.POST,
            transaction=txn,
        )
        if form.is_valid():
            expense = form.cleaned_data["expense"]

            # Defensive double-check
            if expense.payment_account != txn.bank_account:
                raise ValidationError("Expense account mismatch.")

            txn.expense = expense
            txn.offset_account = expense.category.account
            txn.save()

            messages.success(
                request,
                f"Transaction linked to expense “{expense.description}”."
            )
            return redirect(
                "accounting:bankaccount_register",
                txn.bank_account.id,
            )
    else:
        form = BankTransactionLinkExpenseForm(
            transaction=txn,
        )

    return render(
        request,
        "accounting/banktxn_link_expense.html",
        {
            "txn": txn,
            "form": form,
        },
    )