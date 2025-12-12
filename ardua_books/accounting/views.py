
import csv
from datetime import date, timedelta, datetime
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.forms import formset_factory
from django.forms.utils import ErrorList
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy, reverse
from django.views import View
from django.views.generic import CreateView, ListView, DetailView, FormView, TemplateView

from .forms import (
    PaymentForInvoiceForm, 
    PaymentGeneralForm, 
    PaymentAllocationFormSet, 
    PaymentAllocationForm,
    BankTransactionForm,
    BankAccountForm,
    CreatePaymentFromTransactionForm,
    CSVImportForm,
    LinkPaymentToTransactionForm,
)

from .models import (
    ChartOfAccount,
    AccountType,
    JournalEntry, 
    JournalLine, 
    Payment, 
    PaymentApplication,
    PaymentMethod,
    BankAccount, 
    BankTransaction,
    BankImportProfile,
)

from accounting.services.payment_allocation import build_initial_forms_for_invoices, build_formset
from .services.banking import BankAccountService, BankTransactionService
from .services.importing import normalize_amount
from billing.models import Invoice, InvoiceStatus


# ============================================================
# General Ledger/ General Journal Views
# ============================================================

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
    
# ============================================================
# Payment Views
# ============================================================

class PaymentListView(ListView):
    model = Payment
    template_name = "accounting/payment_list.html"
    context_object_name = "payments"

class PaymentDetailView(DetailView):
    model = Payment
    template_name = "accounting/payment_detail.html"
    context_object_name = "payment"
    def get(self, request, *args, **kwargs):
        print("DETAIL VIEW LOADED FOR PAYMENT:", kwargs.get("pk"))
        return super().get(request, *args, **kwargs)

class PaymentCreateGeneralView(View):
    template_name = "accounting/payment_general_form.html"

    # ---------------------------------------------------------
    # GET: empty form (invoice rows appear only after client selection)
    # ---------------------------------------------------------
    def get(self, request):
        header_form = PaymentGeneralForm()
        return render(request, self.template_name, {
            "header_form": header_form,
            "formset": None,
            "invoices": [],
        })

    # ---------------------------------------------------------
    # Helper: rebuild the formset WITH initial invoice metadata
    # ---------------------------------------------------------
    def _build_formset_with_initial(self, request, invoices):
        """
        Create a formset initialized with invoice metadata so the hidden
        invoice_id persists and so the rows do NOT get treated as "empty".
        """
        initial = []
        for inv in invoices:
            initial.append({
                "invoice_id": inv.id,
                "invoice_number": inv.invoice_number,
                "invoice_date": inv.due_date,
                "original_amount": inv.total,
                "outstanding_balance": inv.outstanding_balance(),
            })

        return PaymentAllocationFormSet(request.POST or None, initial=initial)

    # ---------------------------------------------------------
    # POST: full validation + re-render or save + redirect
    # ---------------------------------------------------------
    def post(self, request):

        header_form = PaymentGeneralForm(request.POST)

        # 1. Validate header
        if not header_form.is_valid():
            return render(request, self.template_name, {
                "header_form": header_form,
                "formset": None,
                "invoices": [],
            })

        # Extract cleaned header values
        client = header_form.cleaned_data["client"]
        payment_amount = Decimal(str(header_form.cleaned_data["amount"]))
        payment_date = header_form.cleaned_data["date"]
        payment_method = header_form.cleaned_data["method"]
        payment_memo = header_form.cleaned_data["memo"]

        # Build authoritative invoice list
        invoices = [
            inv for inv in Invoice.objects.filter(client=client,status=InvoiceStatus.ISSUED)
            if inv.outstanding_balance() > 0
        ]
        # 2. Build properly-initialized formset
        formset = self._build_formset_with_initial(request, invoices)

        if not formset.is_valid():
            return render(request, self.template_name, {
                "header_form": header_form,
                "formset": formset,
                "invoices": invoices,
            })

        # 3. Validate allocations
        outstanding_by_id = {inv.id: inv for inv in invoices}
        allocation_list = []
        total_allocated = Decimal("0")

        for i, f in enumerate(formset.forms):
            print(f"ROW {i} cleaned_data: {f.cleaned_data} type: {type(f.cleaned_data)}")

        for form in formset:
            cleaned = form.cleaned_data or {}

            invoice_id = cleaned.get("invoice_id")
            if not invoice_id:
                continue

            amt_raw = cleaned.get("amount_to_apply")
            amt = Decimal(str(amt_raw or "0"))

            # skip empty rows
            if amt == 0:
                continue

            try:
                inv = outstanding_by_id[invoice_id]
            except Exception as e:
                print("   DEBUG: ", e , "occured, ", invoice_id, invoice_id, "not in outstanding_by_id")
                raise
            inv = outstanding_by_id[invoice_id]
            outstanding = inv.outstanding_balance()
            # Individual line validation
            if amt < 0:
                form.add_error("amount_to_apply", "Amount cannot be negative.")
                return render(request, self.template_name, {
                    "header_form": header_form,
                    "formset": formset,
                    "invoices": invoices,
                })

            if amt > outstanding:
                form.add_error(
                    "amount_to_apply",
                    f"Cannot apply more than outstanding balance ({outstanding})."
                )
                return render(request, self.template_name, {
                    "header_form": header_form,
                    "formset": formset,
                    "invoices": invoices,
                })

            # VALID row
            allocation_list.append((inv, amt))
            total_allocated += amt

        # 4. Validate total allocation

        if total_allocated > payment_amount:

            formset._non_form_errors = ErrorList([
                "Total applied cannot exceed payment amount."
            ])
            formset._non_form_error = formset._non_form_errors  # backward compat
            return render(request, self.template_name, {
                "header_form": header_form,
                "formset": formset,
                "invoices": invoices,
            })

        # 5. Save payment + applications
        payment = Payment.objects.create(
            client=client,
            date=payment_date,
            amount=payment_amount,
            method=payment_method,
            memo=payment_memo,
            unapplied_amount=payment_amount,
        )

        for inv, amt in allocation_list:
            if amt > 0:
                PaymentApplication.objects.create(
                    payment=payment,
                    invoice=inv,
                    amount=amt,
                )
                inv.update_status()
                payment.unapplied_amount -= amt

        payment.save()
        payment.post_to_accounting(user=request.user)

        # 6. Redirect
        return redirect("accounting:payment_detail", pk=payment.id)

class PaymentCreateFromTransactionView(View):
    template_name = "accounting/payment_from_transaction_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.txn = get_object_or_404(BankTransaction, pk=kwargs["txn_id"])
        self.bank_account = self.txn.bank_account
        return super().dispatch(request, *args, **kwargs)

    # ----------------------------------------------------
    # GET: Pre-fill payment header from the transaction
    # ----------------------------------------------------
    def get(self, request, txn_id):
        form = PaymentGeneralForm(initial={
            "date": self.txn.date,
            "amount": abs(self.txn.amount),
            "memo": self.txn.description,
            "method": PaymentMethod.CHECK,
        })

        # No invoices until client selected
        return render(request, self.template_name, {
            "header_form": form,
            "formset": None,
            "invoices": [],
            "txn": self.txn,
        })

    # ----------------------------------------------------
    # POST: identical to PaymentCreateGeneralView logic
    # ----------------------------------------------------
    def post(self, request, txn_id):
        txn = self.txn

        header_form = PaymentGeneralForm(request.POST)

        if not header_form.is_valid():
            return render(request, self.template_name, {
                "header_form": header_form,
                "formset": None,
                "invoices": [],
                "txn": txn,
            })

        # Extract payment header
        client = header_form.cleaned_data["client"]
        payment_amount = Decimal(str(header_form.cleaned_data["amount"]))
        payment_date = header_form.cleaned_data["date"]
        payment_method = header_form.cleaned_data["method"]
        payment_memo = header_form.cleaned_data["memo"]

        # Fetch outstanding invoices
        invoices = [
            inv for inv in Invoice.objects.filter(client=client,status=InvoiceStatus.ISSUED)
            if inv.outstanding_balance() > 0
        ]
        if not invoices:
            payment = Payment.objects.create(
                client=client,
                date=payment_date,
                amount=payment_amount,
                method=payment_method,
                memo=payment_memo,
                unapplied_amount=payment_amount,
            )

            payment.post_to_accounting(user=request.user)

            BankTransactionService.link_existing_payment(txn, payment)

            messages.success(request, "Payment created and linked to transaction.")
            return redirect("accounting:bankaccount_register", pk=self.bank_account.pk)
        # Rebuild formset with invoice metadata
        formset = build_formset(request, invoices)

        if not formset.is_valid():
            return render(request, self.template_name, {
                "header_form": header_form,
                "formset": formset,
                "invoices": invoices,
                "txn": txn,
            })

        # Validate allocations
        outstanding_by_id = {inv.id: inv for inv in invoices}
        allocation_list = []
        total_allocated = Decimal("0")

        for form in formset:
            cleaned = form.cleaned_data or {}
            invoice_id = cleaned.get("invoice_id")
            if not invoice_id:
                continue

            amt = Decimal(str(cleaned.get("amount_to_apply") or "0"))
            if amt == 0:
                continue

            inv = outstanding_by_id[invoice_id]
            if amt < 0:
                form.add_error("amount_to_apply", "Amount cannot be negative.")
                return render(request, self.template_name, {
                    "header_form": header_form,
                    "formset": formset,
                    "invoices": invoices,
                    "txn": txn,
                })

            if amt > inv.outstanding_balance():
                form.add_error("amount_to_apply", f"Cannot exceed outstanding balance.")
                return render(request, self.template_name, {
                    "header_form": header_form,
                    "formset": formset,
                    "invoices": invoices,
                    "txn": txn,
                })

            allocation_list.append((inv, amt))
            total_allocated += amt

        if total_allocated > payment_amount:
            formset._non_form_errors = ErrorList(["Total applied cannot exceed payment amount."])
            return render(request, self.template_name, {
                "header_form": header_form,
                "formset": formset,
                "invoices": invoices,
                "txn": txn,
            })

        # ----------------------------------------------------
        # CREATE PAYMENT
        # ----------------------------------------------------
        payment = Payment.objects.create(
            client=client,
            date=payment_date,
            amount=payment_amount,
            method=payment_method,
            memo=payment_memo,
            unapplied_amount=payment_amount,
        )

        for inv, amt in allocation_list:
            if amt > 0:
                PaymentApplication.objects.create(
                    payment=payment,
                    invoice=inv,
                    amount=amt,
                )
                inv.update_status()
                payment.unapplied_amount -= amt
                

        payment.save()
        payment.post_to_accounting(user=request.user)

        # ----------------------------------------------------
        # LINK PAYMENT TO BANK TRANSACTION
        # ----------------------------------------------------
        BankTransactionService.link_existing_payment(txn, payment)

        messages.success(request, "Payment created and linked to transaction.")
        return redirect("accounting:bankaccount_register", pk=self.bank_account.pk)

    
class PaymentCreateForInvoiceView(FormView):
    template_name = "accounting/payment_for_invoice_form.html"
    form_class = PaymentForInvoiceForm

    def dispatch(self, request, *args, **kwargs):
        self.invoice = get_object_or_404(Invoice, id=kwargs["invoice_id"])
        self.client = self.invoice.client
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["client"] = self.client
        return kwargs

    def form_valid(self, form):
        # Create the Payment
        payment = Payment.objects.create(
            client=self.client,
            date=form.cleaned_data["date"],
            amount=form.cleaned_data["amount"],
            unapplied_amount=form.cleaned_data["amount"],  # initially
            method=form.cleaned_data["method"],
            memo=form.cleaned_data["memo"],
        )

        # Apply to this single invoice
        amount_to_apply = min(payment.unapplied_amount, self.invoice.outstanding_balance())

        PaymentApplication.objects.create(
            payment=payment,
            invoice=self.invoice,
            amount=amount_to_apply,
        )
        self.invoice.update_status()
        # Reduce unpaid portion on the payment
        payment.unapplied_amount -= amount_to_apply
        payment.save()
        payment.post_to_accounting(user=self.request.user)


        # TODO: later, trigger GL posting here

        self.payment = payment
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("billing:invoice_detail", args=[self.invoice.id])


def payment_invoice_fragment(request):
    """
    Returns just the invoice allocation rows for htmx.
    """
    client_id = request.GET.get("client")

    if not client_id:
        return render(request, "accounting/payment_invoice_rows.html", {
            "invoices": None,
            "formset": None,
        })

    invoices = [
        inv for inv in Invoice.objects.filter(client_id=client_id, status=InvoiceStatus.ISSUED)
        if inv.outstanding_balance() > 0
    ]

    PaymentAllocationFormSet = formset_factory(PaymentAllocationForm, extra=0)

    initial = [{
        "invoice_id": inv.id,
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.issue_date,
        "original_amount": inv.total,
        "outstanding_balance": inv.outstanding_balance(),
        "amount_to_apply": Decimal("0.00")
    } for inv in invoices]

    formset = PaymentAllocationFormSet(initial=initial)

    return render(
        request,
        "accounting/payment_invoice_rows.html",
        {
            "invoices": invoices,
            "formset": formset,
        },
    )

# ============================================================
# Banking Views
# ============================================================

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

        # If POST, restore filtered queryset
        if self.request.method == "POST":
            amount = self.request.POST.get("amount")
            try:
                amt = Decimal(amount)
            except Exception:
                amt = None

            form.cleaned_data = {}  # safe placeholder
            form.cleaned_data["amount"] = amt

            # Repopulate queryset using the same filtering logic
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
    def get(self, request, *args, **kwargs):
        amount_raw = request.GET.get("amount")
        bank_account_id = request.GET.get("bank_account")

        qs = ChartOfAccount.objects.none()

        try:
            amount = Decimal(amount_raw)
        except Exception:
            amount = None

        # Exclude the target bank account itself
        excluded_ids = []
        if bank_account_id:
            try:
                excluded_ids.append(int(bank_account_id))
            except ValueError:
                pass

        if amount is not None:
            # Deposit
            if amount > 0:
                qs = ChartOfAccount.objects.filter(
                    type__in=[
                        AccountType.INCOME,
                        AccountType.LIABILITY,
                        AccountType.EQUITY,
                    ]
                )

            # Withdrawal
            elif amount < 0:
                qs = ChartOfAccount.objects.filter(
                    type__in=[
                        AccountType.EXPENSE,
                        AccountType.LIABILITY,
                        AccountType.EQUITY,
                    ]
                )

            # Exclude the bank account itself
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
        coa = bank_account.account

        today = date.today()

        #
        # 1. Determine filter range from GET params
        #
        range_code = self.request.GET.get("range")
        from_str = self.request.GET.get("from")
        to_str = self.request.GET.get("to")

        # Default: last 30 days
        if not range_code and not from_str and not to_str:
            range_code = "last30"

        # Apply preset ranges
        if range_code == "last30":
            from_date = today - timedelta(days=30)
            to_date = today

        elif range_code == "last90":
            from_date = today - timedelta(days=90)
            to_date = today

        elif range_code == "month":  # month-to-date
            from_date = today.replace(day=1)
            to_date = today

        elif range_code == "ytd":  # year-to-date
            from_date = today.replace(month=1, day=1)
            to_date = today

        elif range_code == "all":
            from_date = None
            to_date = None

        else:
            # Manual custom date range
            from_date = date.fromisoformat(from_str) if from_str else None
            to_date = date.fromisoformat(to_str) if to_str else None

        #
        # 2. Query filtered transactions
        #
        tx_qs = BankTransaction.objects.filter(bank_account=bank_account)

        if from_date:
            tx_qs = tx_qs.filter(date__gte=from_date)
        if to_date:
            tx_qs = tx_qs.filter(date__lte=to_date)

        tx_qs = tx_qs.order_by("date", "id")
        transactions = list(tx_qs)

        #
        # 3. Compute balance forward
        #
        # All transactions before the filtered range
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

        #
        # 4. Running balance for displayed transactions
        #
        running = balance_forward
        for txn in transactions:
            running += txn.amount
            txn.running_balance = running

        #
        # 5. Provide data to the template
        #
        ctx.update(
            {
                "bank_account": bank_account,
                "transactions": transactions,
                "opening_balance": opening_balance,
                "balance_forward": balance_forward,
                "from_date": from_date,
                "to_date": to_date,
                "range_code": range_code,
            }
        )
        return ctx
    
class BankTransactionDetailView(DetailView):
    model = BankTransaction
    template_name = "accounting/banktransaction_detail.html"
    context_object_name = "txn"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # Convenience access
        txn = ctx["txn"]

        # Determine withdrawal / deposit amounts for display
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

        # Load import profile
        try:
            profile = account.import_profile
        except BankImportProfile.DoesNotExist:
            messages.error(request, "No import profile defined for this account.")
            return redirect("accounting:bankaccount_detail", pk=account.pk)

        # Process CSV
        decoded = csv_file.read().decode("utf-8").splitlines()
        reader = csv.reader(decoded)

        count = 0

        for row in reader:
            # Skip empty rows
            if not row:
                continue

            # Skip header rows for CSVs that include them
            # (Idea: if date column index contains letters, it's a header)
            if not row[profile.date_column_index].strip()[0].isdigit():
                # crude but effective; can refine later
                continue

            raw_date = row[profile.date_column_index].strip()
            raw_desc = row[profile.description_column_index].strip()
            raw_amount = row[profile.amount_column_index].strip()

            # Skip junk rows
            if profile.skip_if_description_contains:
                if profile.skip_if_description_contains.lower() in raw_desc.lower():
                    continue

            # Parse fields
            dt = datetime.strptime(raw_date, profile.date_format).date()
            amt = Decimal(raw_amount)

            # Apply sign rule
            amt = normalize_amount(amt, profile)

            # Create transaction
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
            BankTransactionService.link_existing_payment(
                txn=txn,
                payment=payment,
            )
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
