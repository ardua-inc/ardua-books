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
    BankTransactionMatchTransferForm,
    ExpenseMatchFormSet,
    PaymentMatchFormSet,
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
from accounting.views.mixins import FilterPersistenceMixin


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
        return redirect("accounting:bankaccount_register", pk=self.bank_account.pk)

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


class BankRegisterView(FilterPersistenceMixin, TemplateView):
    template_name = "accounting/bank_register.html"

    # Filter persistence settings
    filter_params = ["date_preset", "date_from", "date_to", "show", "per_page", "sort"]

    # Pagination settings
    DEFAULT_PAGE_SIZE = 50
    PAGE_SIZE_OPTIONS = [20, 50, 100, 200]

    def get_filter_persistence_key(self):
        # Include bank account ID so each account has its own saved filters
        return f"bank_register_filters_{self.kwargs.get('pk')}"

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator

        ctx = super().get_context_data(**kwargs)

        bank_account = get_object_or_404(BankAccount, pk=self.kwargs["pk"])
        all_accounts = BankAccount.objects.all().order_by("institution")
        today = date.today()

        # Get filter parameters
        date_preset = self.request.GET.get("date_preset", "last30")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")
        show_filter = self.request.GET.get("show", "all")

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
        elif date_preset == "all":
            from_date = None
            to_date = None
        elif date_from or date_to:
            # Custom range
            from_date = date.fromisoformat(date_from) if date_from else None
            to_date = date.fromisoformat(date_to) if date_to else None
            date_preset = ""
        else:
            # Default to last 30 days
            from_date = today - timedelta(days=30)
            to_date = today

        # Query transactions
        tx_qs = BankTransaction.objects.filter(bank_account=bank_account).select_related(
            "payment", "expense", "expense__category", "offset_account",
            "transfer_pair", "transfer_pair__bank_account"
        )

        if from_date:
            tx_qs = tx_qs.filter(date__gte=from_date)
        if to_date:
            tx_qs = tx_qs.filter(date__lte=to_date)

        # Filter by matched/unmatched
        if show_filter == "unmatched":
            tx_qs = tx_qs.filter(payment__isnull=True, expense__isnull=True, transfer_pair__isnull=True)
        elif show_filter == "matched":
            from django.db.models import Q
            tx_qs = tx_qs.filter(Q(payment__isnull=False) | Q(expense__isnull=False) | Q(transfer_pair__isnull=False))

        # Handle sorting
        sort_param = self.request.GET.get("sort", "date")
        sort_field = sort_param.lstrip("-")
        sort_desc = sort_param.startswith("-")

        if sort_field == "description":
            tx_qs = tx_qs.order_by("-description" if sort_desc else "description", "date", "id")
        else:
            # Default: sort by date
            tx_qs = tx_qs.order_by("-date" if sort_desc else "date", "id")

        # Calculate balance forward (sum of all transactions before from_date)
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

        # Pagination
        page_size = self.request.GET.get("per_page", self.DEFAULT_PAGE_SIZE)
        try:
            page_size = int(page_size)
            if page_size not in self.PAGE_SIZE_OPTIONS:
                page_size = self.DEFAULT_PAGE_SIZE
        except (ValueError, TypeError):
            page_size = self.DEFAULT_PAGE_SIZE

        # Get all transactions for running balance calculation
        all_transactions = list(tx_qs)

        # Calculate running balances
        running = balance_forward
        for txn in all_transactions:
            running += txn.amount
            txn.running_balance = running

        # Paginate
        paginator = Paginator(all_transactions, page_size)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        ctx.update({
            "bank_account": bank_account,
            "all_accounts": all_accounts,
            "transactions": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "opening_balance": opening_balance,
            "balance_forward": balance_forward,
            "date_preset": date_preset,
            "date_from": date_from if not date_preset else "",
            "date_to": date_to if not date_preset else "",
            "show_filter": show_filter,
            "per_page": page_size,
            "page_size_options": self.PAGE_SIZE_OPTIONS,
            "sort": sort_param,
            "sort_field": sort_field,
            "sort_desc": sort_desc,
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
            return redirect("accounting:bankaccount_register", pk=account.pk)

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
        return redirect("accounting:bankaccount_register", pk=account.pk)


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
    from billing.models import Expense

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
            create_new = form.cleaned_data.get("create_new")

            if create_new:
                # Create a new expense from the bank transaction
                category = form.cleaned_data["category"]
                expense = Expense.objects.create(
                    client=None,  # Not client-specific
                    category=category,
                    expense_date=txn.date,
                    amount=abs(txn.amount),
                    description=txn.description,
                    billable=False,  # Non-client expenses are not billable
                )
            else:
                expense = form.cleaned_data["expense"]

            try:
                BankTransactionService.link_expense(txn=txn, expense=expense)
                if create_new:
                    messages.success(
                        request,
                        f'Created expense "{expense.description}" and posted to GL.'
                    )
                else:
                    messages.success(
                        request,
                        f'Transaction linked to expense "{expense.description}" and posted to GL.'
                    )
            except ValueError as e:
                messages.error(request, str(e))
                # If we created a new expense but linking failed, delete it
                if create_new:
                    expense.delete()
                return render(
                    request,
                    "accounting/banktxn_link_expense.html",
                    {"txn": txn, "form": form},
                )

            return redirect(
                "accounting:bankaccount_register",
                txn.bank_account.id,
            )
    else:
        form = BankTransactionLinkExpenseForm(
            transaction=txn,
        )

    # Check if there are any matching expenses
    has_matching_expenses = form.fields["expense"].queryset.exists()

    return render(
        request,
        "accounting/banktxn_link_expense.html",
        {
            "txn": txn,
            "form": form,
            "has_matching_expenses": has_matching_expenses,
        },
    )


@login_required
def banktransaction_match_transfer(request, pk):
    """Match a bank transaction to another transaction as an inter-account transfer."""
    txn = get_object_or_404(BankTransaction, pk=pk)

    # Check if already matched
    if txn.is_matched:
        messages.error(request, "This transaction is already matched.")
        return redirect("accounting:bankaccount_register", txn.bank_account.id)

    if request.method == "POST":
        form = BankTransactionMatchTransferForm(
            request.POST,
            source_transaction=txn,
        )
        if form.is_valid():
            target_txn = form.cleaned_data["target_transaction"]

            try:
                BankTransactionService.match_transfer(txn_from=txn, txn_to=target_txn)
                messages.success(
                    request,
                    f"Matched transfer between {txn.bank_account.institution} and {target_txn.bank_account.institution}."
                )
            except ValueError as e:
                messages.error(request, str(e))
                return render(
                    request,
                    "accounting/banktxn_match_transfer.html",
                    {"txn": txn, "form": form},
                )

            return redirect("accounting:bankaccount_register", txn.bank_account.id)
    else:
        form = BankTransactionMatchTransferForm(source_transaction=txn)

    # Check if there are any matching transactions
    has_matching_transactions = form.fields["target_transaction"].queryset.exists()

    return render(
        request,
        "accounting/banktxn_match_transfer.html",
        {
            "txn": txn,
            "form": form,
            "has_matching_transactions": has_matching_transactions,
        },
    )


class BatchMatchExpensesView(FilterPersistenceMixin, TemplateView):
    """
    Batch matching view for expense transactions.
    Shows all unmatched withdrawals and allows selecting an existing expense
    or creating a new expense by category for each transaction.
    """
    template_name = "accounting/batch_match_expenses.html"

    filter_params = ["date_preset", "date_from", "date_to", "per_page", "sort"]
    DEFAULT_PAGE_SIZE = 50
    PAGE_SIZE_OPTIONS = [20, 50, 100, 200]

    def get_filter_persistence_key(self):
        return f"batch_match_expenses_filters_{self.kwargs.get('pk')}"

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator
        from billing.models import Expense

        ctx = super().get_context_data(**kwargs)

        bank_account = get_object_or_404(BankAccount, pk=self.kwargs["pk"])
        today = date.today()

        # Get filter parameters
        date_preset = self.request.GET.get("date_preset", "last90")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

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
        elif date_preset == "all":
            from_date = None
            to_date = None
        elif date_from or date_to:
            from_date = date.fromisoformat(date_from) if date_from else None
            to_date = date.fromisoformat(date_to) if date_to else None
            date_preset = ""
        else:
            from_date = today - timedelta(days=90)
            to_date = today

        # Query unmatched withdrawals (amount < 0)
        tx_qs = BankTransaction.objects.filter(
            bank_account=bank_account,
            amount__lt=0,  # Withdrawals only
            payment__isnull=True,
            expense__isnull=True,
            transfer_pair__isnull=True,
        )

        if from_date:
            tx_qs = tx_qs.filter(date__gte=from_date)
        if to_date:
            tx_qs = tx_qs.filter(date__lte=to_date)

        # Handle sorting
        sort_param = self.request.GET.get("sort", "date")
        sort_field = sort_param.lstrip("-")
        sort_desc = sort_param.startswith("-")

        if sort_field == "description":
            tx_qs = tx_qs.order_by("-description" if sort_desc else "description", "date", "id")
        else:
            # Default: sort by date
            tx_qs = tx_qs.order_by("-date" if sort_desc else "date", "id")

        # Pagination
        page_size = self.request.GET.get("per_page", self.DEFAULT_PAGE_SIZE)
        try:
            page_size = int(page_size)
            if page_size not in self.PAGE_SIZE_OPTIONS:
                page_size = self.DEFAULT_PAGE_SIZE
        except (ValueError, TypeError):
            page_size = self.DEFAULT_PAGE_SIZE

        all_transactions = list(tx_qs)

        paginator = Paginator(all_transactions, page_size)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        # Build form data for each transaction on this page
        form_data = []
        for txn in page_obj:
            # Find potential expense matches (same absolute amount, not linked)
            potential_expenses = (
                Expense.objects
                .filter(payment_account__isnull=True)
                .filter(amount=abs(txn.amount))
                .select_related("category", "client")
                .order_by("-expense_date")
            )

            # Build choices for the expense dropdown
            expense_choices = [("", "-- Select --")]
            for exp in potential_expenses:
                client_name = exp.client.name if exp.client else "No Client"
                label = f"{exp.expense_date} | {exp.category.name} | {client_name} | ${exp.amount}"
                expense_choices.append((str(exp.id), label))

            form_data.append({
                "txn": txn,
                "expense_choices": expense_choices,
                "has_matches": len(expense_choices) > 1,
            })

        # Build formset with initial data
        initial = [
            {"transaction_id": item["txn"].id}
            for item in form_data
        ]
        formset = ExpenseMatchFormSet(initial=initial, prefix="expenses")

        # Manually set expense choices for each form
        for i, form in enumerate(formset.forms):
            form.fields["expense"].choices = form_data[i]["expense_choices"]

        # Zip forms with transaction data
        forms_with_data = list(zip(formset.forms, form_data))

        ctx.update({
            "bank_account": bank_account,
            "formset": formset,
            "forms_with_data": forms_with_data,
            "page_obj": page_obj,
            "paginator": paginator,
            "date_preset": date_preset,
            "date_from": date_from if not date_preset else "",
            "date_to": date_to if not date_preset else "",
            "per_page": page_size,
            "page_size_options": self.PAGE_SIZE_OPTIONS,
            "total_unmatched": paginator.count,
            "sort": sort_param,
            "sort_field": sort_field,
            "sort_desc": sort_desc,
        })
        return ctx

    def post(self, request, *args, **kwargs):
        from billing.models import Expense

        bank_account = get_object_or_404(BankAccount, pk=self.kwargs["pk"])

        formset = ExpenseMatchFormSet(request.POST, prefix="expenses")

        matched_count = 0
        created_count = 0
        errors = []

        if formset.is_valid():
            with transaction.atomic():
                for form in formset.forms:
                    txn_id = form.cleaned_data.get("transaction_id")
                    expense_id = form.cleaned_data.get("expense")
                    category = form.cleaned_data.get("category")

                    if not expense_id and not category:
                        continue  # No action for this row

                    try:
                        txn = BankTransaction.objects.get(pk=txn_id, bank_account=bank_account)
                    except BankTransaction.DoesNotExist:
                        errors.append(f"Transaction {txn_id} not found.")
                        continue

                    # Skip if already matched
                    if txn.is_matched:
                        continue

                    try:
                        if expense_id:
                            # Link to existing expense
                            expense = Expense.objects.get(pk=expense_id)
                            BankTransactionService.link_expense(txn=txn, expense=expense)
                            matched_count += 1
                        elif category:
                            # Create new expense and link it
                            expense = Expense.objects.create(
                                client=None,
                                category=category,
                                expense_date=txn.date,
                                amount=abs(txn.amount),
                                description=txn.description,
                                billable=False,
                            )
                            BankTransactionService.link_expense(txn=txn, expense=expense)
                            created_count += 1
                    except Exception as e:
                        errors.append(f"Error processing transaction {txn_id}: {e}")

        if matched_count or created_count:
            msg_parts = []
            if matched_count:
                msg_parts.append(f"linked {matched_count} expense(s)")
            if created_count:
                msg_parts.append(f"created {created_count} new expense(s)")
            messages.success(request, "Successfully " + " and ".join(msg_parts) + ".")

        if errors:
            for error in errors[:5]:  # Show first 5 errors
                messages.error(request, error)

        # Redirect back to the same page with filters preserved
        return redirect(request.get_full_path())


class BatchMatchPaymentsView(FilterPersistenceMixin, TemplateView):
    """
    Batch matching view for payment transactions.
    Shows all unmatched deposits and allows selecting an existing payment
    to link to each transaction.
    """
    template_name = "accounting/batch_match_payments.html"

    filter_params = ["date_preset", "date_from", "date_to", "per_page", "sort"]
    DEFAULT_PAGE_SIZE = 50
    PAGE_SIZE_OPTIONS = [20, 50, 100, 200]

    def get_filter_persistence_key(self):
        return f"batch_match_payments_filters_{self.kwargs.get('pk')}"

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator
        from accounting.models import Payment

        ctx = super().get_context_data(**kwargs)

        bank_account = get_object_or_404(BankAccount, pk=self.kwargs["pk"])
        today = date.today()

        # Get filter parameters
        date_preset = self.request.GET.get("date_preset", "last90")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

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
        elif date_preset == "all":
            from_date = None
            to_date = None
        elif date_from or date_to:
            from_date = date.fromisoformat(date_from) if date_from else None
            to_date = date.fromisoformat(date_to) if date_to else None
            date_preset = ""
        else:
            from_date = today - timedelta(days=90)
            to_date = today

        # Query unmatched deposits (amount > 0)
        tx_qs = BankTransaction.objects.filter(
            bank_account=bank_account,
            amount__gt=0,  # Deposits only
            payment__isnull=True,
            expense__isnull=True,
            transfer_pair__isnull=True,
        )

        if from_date:
            tx_qs = tx_qs.filter(date__gte=from_date)
        if to_date:
            tx_qs = tx_qs.filter(date__lte=to_date)

        # Handle sorting
        sort_param = self.request.GET.get("sort", "date")
        sort_field = sort_param.lstrip("-")
        sort_desc = sort_param.startswith("-")

        if sort_field == "description":
            tx_qs = tx_qs.order_by("-description" if sort_desc else "description", "date", "id")
        else:
            # Default: sort by date
            tx_qs = tx_qs.order_by("-date" if sort_desc else "date", "id")

        # Pagination
        page_size = self.request.GET.get("per_page", self.DEFAULT_PAGE_SIZE)
        try:
            page_size = int(page_size)
            if page_size not in self.PAGE_SIZE_OPTIONS:
                page_size = self.DEFAULT_PAGE_SIZE
        except (ValueError, TypeError):
            page_size = self.DEFAULT_PAGE_SIZE

        all_transactions = list(tx_qs)

        paginator = Paginator(all_transactions, page_size)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        # Build form data for each transaction on this page
        form_data = []
        for txn in page_obj:
            # Find potential payment matches (same amount, not linked to bank txn)
            potential_payments = (
                Payment.objects
                .filter(bank_transactions__isnull=True)
                .filter(amount=txn.amount)
                .select_related("client")
                .order_by("-date")
            )

            # Build choices for the payment dropdown
            payment_choices = [("", "-- Select --")]
            for pmt in potential_payments:
                label = f"{pmt.date} | {pmt.client.name} | ${pmt.amount} | {pmt.memo or 'No memo'}"
                payment_choices.append((str(pmt.id), label))

            form_data.append({
                "txn": txn,
                "payment_choices": payment_choices,
                "has_matches": len(payment_choices) > 1,
            })

        # Build formset with initial data
        initial = [
            {"transaction_id": item["txn"].id}
            for item in form_data
        ]
        formset = PaymentMatchFormSet(initial=initial, prefix="payments")

        # Manually set payment choices for each form
        for i, form in enumerate(formset.forms):
            form.fields["payment"].choices = form_data[i]["payment_choices"]

        # Zip forms with transaction data
        forms_with_data = list(zip(formset.forms, form_data))

        ctx.update({
            "bank_account": bank_account,
            "formset": formset,
            "forms_with_data": forms_with_data,
            "page_obj": page_obj,
            "paginator": paginator,
            "date_preset": date_preset,
            "date_from": date_from if not date_preset else "",
            "date_to": date_to if not date_preset else "",
            "per_page": page_size,
            "page_size_options": self.PAGE_SIZE_OPTIONS,
            "total_unmatched": paginator.count,
            "sort": sort_param,
            "sort_field": sort_field,
            "sort_desc": sort_desc,
        })
        return ctx

    def post(self, request, *args, **kwargs):
        from accounting.models import Payment

        bank_account = get_object_or_404(BankAccount, pk=self.kwargs["pk"])

        formset = PaymentMatchFormSet(request.POST, prefix="payments")

        matched_count = 0
        errors = []

        if formset.is_valid():
            with transaction.atomic():
                for form in formset.forms:
                    txn_id = form.cleaned_data.get("transaction_id")
                    payment_id = form.cleaned_data.get("payment")

                    if not payment_id:
                        continue  # No action for this row

                    try:
                        txn = BankTransaction.objects.get(pk=txn_id, bank_account=bank_account)
                    except BankTransaction.DoesNotExist:
                        errors.append(f"Transaction {txn_id} not found.")
                        continue

                    # Skip if already matched
                    if txn.is_matched:
                        continue

                    try:
                        payment = Payment.objects.get(pk=payment_id)
                        BankTransactionService.link_existing_payment(txn=txn, payment=payment)
                        matched_count += 1
                    except Exception as e:
                        errors.append(f"Error processing transaction {txn_id}: {e}")

        if matched_count:
            messages.success(request, f"Successfully linked {matched_count} payment(s).")

        if errors:
            for error in errors[:5]:  # Show first 5 errors
                messages.error(request, error)

        # Redirect back to the same page with filters preserved
        return redirect(request.get_full_path())