
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
        required=False,
        label="Expense",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    create_new = forms.BooleanField(
        required=False,
        label="Create and link new expense",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    category = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label="Expense Category",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, **kwargs):
        from billing.models import ExpenseCategory
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

        # Category choices for creating new expense
        self.fields["category"].queryset = (
            ExpenseCategory.objects
            .filter(account__isnull=False)  # Only categories with GL accounts
            .order_by("name")
        )

    def clean(self):
        cleaned_data = super().clean()
        create_new = cleaned_data.get("create_new")
        expense = cleaned_data.get("expense")
        category = cleaned_data.get("category")

        if create_new:
            if not category:
                raise forms.ValidationError("Please select an expense category.")
        else:
            if not expense:
                raise forms.ValidationError("Please select an expense to link.")

        return cleaned_data

class BankTransactionMatchTransferForm(forms.Form):
    """Form to select a matching transaction for inter-account transfer."""
    target_transaction = forms.ModelChoiceField(
        queryset=None,
        widget=forms.Select(attrs={"class": "form-select"}),
        required=True,
        label="Matching Transaction",
    )

    def __init__(self, *args, **kwargs):
        from accounting.models import BankTransaction
        source_txn = kwargs.pop("source_transaction")
        super().__init__(*args, **kwargs)

        # Find candidate transactions:
        # - Different bank account
        # - Same absolute amount
        # - Not already matched (no payment, expense, or transfer_pair)
        amt = abs(source_txn.amount)
        self.fields["target_transaction"].queryset = (
            BankTransaction.objects
            .exclude(bank_account=source_txn.bank_account)
            .filter(payment__isnull=True)
            .filter(expense__isnull=True)
            .filter(transfer_pair__isnull=True)
            .filter(amount__in=[amt, -amt])
            .select_related("bank_account")
            .order_by("-date")
        )

    def label_from_instance(self, obj):
        """Custom label for the transaction dropdown."""
        return f"{obj.date} | {obj.bank_account.institution} | {obj.description} | ${abs(obj.amount)}"


class CSVImportForm(forms.Form):
    file = forms.FileField()
    offset_account = forms.ModelChoiceField(
        queryset=ChartOfAccount.objects.all(),
        help_text="Choose the account that offsets these imported transactions.",
    )


class ExpenseMatchRowForm(forms.Form):
    """
    Form for a single row in the batch expense matching table.
    Each row corresponds to one unmatched withdrawal transaction.
    """
    transaction_id = forms.IntegerField(widget=forms.HiddenInput)
    expense = forms.ChoiceField(
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    category = forms.ModelChoiceField(
        queryset=None,
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )

    def __init__(self, *args, expense_choices=None, **kwargs):
        from billing.models import ExpenseCategory
        super().__init__(*args, **kwargs)

        # Set expense choices dynamically (passed from view)
        if expense_choices:
            self.fields["expense"].choices = expense_choices
        else:
            self.fields["expense"].choices = [("", "-- Select --")]

        # Category choices for creating new expense
        self.fields["category"].queryset = (
            ExpenseCategory.objects
            .filter(account__isnull=False)
            .order_by("name")
        )
        self.fields["category"].empty_label = "-- Create New --"

    def clean(self):
        cleaned_data = super().clean()
        expense = cleaned_data.get("expense")
        category = cleaned_data.get("category")

        # Both can be empty (no action taken for this row)
        # But if both are filled, that's an error
        if expense and category:
            raise forms.ValidationError(
                "Select either an existing expense OR a category to create new, not both."
            )

        return cleaned_data


ExpenseMatchFormSet = formset_factory(
    ExpenseMatchRowForm,
    extra=0,
)


class PaymentMatchRowForm(forms.Form):
    """
    Form for a single row in the batch payment matching table.
    Each row corresponds to one unmatched deposit transaction.
    """
    transaction_id = forms.IntegerField(widget=forms.HiddenInput)
    payment = forms.ChoiceField(
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )

    def __init__(self, *args, payment_choices=None, **kwargs):
        super().__init__(*args, **kwargs)

        # Set payment choices dynamically (passed from view)
        if payment_choices:
            self.fields["payment"].choices = payment_choices
        else:
            self.fields["payment"].choices = [("", "-- Select --")]


PaymentMatchFormSet = formset_factory(
    PaymentMatchRowForm,
    extra=0,
)