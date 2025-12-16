
from django import forms
from django.forms import formset_factory
from .models import Payment, PaymentMethod, BankAccountType, ChartOfAccount, AccountType
from billing.models import Client, Invoice, Expense


class PaymentForInvoiceForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ["date", "amount", "method", "memo"]

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = client

class PaymentGeneralForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ["client", "date", "amount", "method", "memo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["client"].queryset = Client.objects.all().order_by("name")

        # Only apply initial defaults on GET, not POST
        if not self.is_bound:
            self.fields["date"].initial = forms.fields.datetime.date.today

            # Choose first method by default
            method_choices = self.fields["method"].choices
            if method_choices:
                self.fields["method"].initial = method_choices[1][0]  # skip blank


class PaymentAllocationForm(forms.Form):
    def has_changed(self):
        return True
    invoice_id = forms.IntegerField(widget=forms.HiddenInput)
    amount_to_apply = forms.DecimalField(required=False, min_value=0)

PaymentAllocationFormSet = formset_factory(
    PaymentAllocationForm,
    extra=0,
)

class CreatePaymentFromTransactionForm(forms.Form):
    client = forms.ModelChoiceField(
        queryset=Client.objects.all().order_by("name"),
        widget=forms.Select(attrs={"class": "form-select"}),
        required=True,
    )

    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "readonly": "readonly"}),
    )

    method = forms.ChoiceField(
        choices=PaymentMethod.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
        required=True,
    )

    memo = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    invoices = forms.ModelMultipleChoiceField(
        queryset=Invoice.objects.none(),   # dynamically populated in __init__
        widget=forms.SelectMultiple(attrs={"class": "form-select"}),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        client = kwargs.pop("client", None)
        super().__init__(*args, **kwargs)

        # Pre-fill invoice dropdown if client is known (view will pass it)
        if client:
            self.fields["invoices"].queryset = (
                Invoice.objects.filter(client=client)
                .exclude(status="PAID")
                .order_by("-issue_date")
            )

class LinkPaymentToTransactionForm(forms.Form):
    payment = forms.ModelChoiceField(
        queryset=Payment.objects.none(),  # populated in __init__
        widget=forms.Select(attrs={"class": "form-select"}),
        required=True,
    )

    def __init__(self, *args, **kwargs):
        txn = kwargs.pop("txn")
        super().__init__(*args, **kwargs)

        amt = txn.amount
        client = None

        # If txn already has a payment, form should not be used
        if txn.payment:
            self.fields["payment"].queryset = Payment.objects.none()
            return

        # If txn description hints at client, we could parse it later…
        # For now, do not infer.

        qs = Payment.objects.filter(amount=amt)

        # Payments not already linked to a bank transaction
        qs = qs.filter(bank_transactions__isnull=True)

        self.fields["payment"].queryset = qs.order_by("-date")

class BankAccountForm(forms.Form):
    """
    This is intentionally NOT a ModelForm because BankAccount
    creation involves coordinated service-layer operations
    (COA creation + opening balance JE).
    """

    type = forms.ChoiceField(
        choices=BankAccountType.choices,
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    institution = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    account_number_masked = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        help_text="Use a masked value such as ***1234",
    )

    opening_balance = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        initial=0,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )


class BankTransactionForm(forms.Form):
    date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )

    description = forms.CharField(
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
        help_text="Positive = deposit; Negative = withdrawal/charge",
    )

    offset_account = forms.ModelChoiceField(
        queryset=ChartOfAccount.objects.none(),
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text="Choose the category to offset this transaction",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Initially set queryset to empty; will be filtered later
        self.fields["offset_account"].queryset = ChartOfAccount.objects.none()
     
    def filter_accounts(self, bank_account=None):
        amount = self.cleaned_data.get("amount")

        qs = ChartOfAccount.objects.none()

        if amount is None:
            self.fields["offset_account"].queryset = qs
            return

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
        else:
            qs = ChartOfAccount.objects.filter(
                type__in=[
                    AccountType.EXPENSE,
                    AccountType.LIABILITY,
                    AccountType.EQUITY,
                ]
            )

        # Exclude the bank account itself
        if bank_account is not None:
            qs = qs.exclude(id=bank_account.account.id)

        self.fields["offset_account"].queryset = qs.order_by("type", "code")

class BankTransactionLinkExpenseForm(forms.Form):
    expense = forms.ModelChoiceField(
        queryset=Expense.objects.none(),
        required=True,
        label="Expense",
    )

    def __init__(self, *args, **kwargs):
        txn = kwargs.pop("transaction")
        super().__init__(*args, **kwargs)

        # Candidate expenses:
        # - Not already linked to a bank transaction
        # - payment_account not set yet (unlinked) or null
        # - Amount matches (abs)
        self.fields["expense"].queryset = (
            Expense.objects
            .filter(payment_account__isnull=True)  # Not yet linked to any bank account
            .filter(bank_transactions__isnull=True)  # Not already matched to a bank txn
            .filter(amount=abs(txn.amount))
            .select_related("category")
            .order_by("-expense_date")
        )

class CSVImportForm(forms.Form):
    file = forms.FileField()
    offset_account = forms.ModelChoiceField(
        queryset=ChartOfAccount.objects.all(),
        help_text="Choose the account that offsets these imported transactions.",
    )