from django import forms
from django.forms import inlineformset_factory, BaseInlineFormSet

from .models import (
    Client,
    TimeEntry,
    Expense,
    Invoice,
    InvoiceLine,
)


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = [
            "name",
            "billing_address",
            "email",
            "phone",
            "default_hourly_rate",
            "payment_terms_days",
            "is_active",
        ]
        widgets = {
            "billing_address": forms.Textarea(attrs={"rows": 3}),
        }


class TimeEntryForm(forms.ModelForm):
    billing_rate = forms.DecimalField(
        max_digits=8,
        decimal_places=2,
        required=False,
        help_text="Leave blank to use the client's default hourly rate.",
    )

    class Meta:
        model = TimeEntry
        fields = [
            "client",
            "consultant",
            "work_date",
            "hours",
            "description",
            "billing_rate",
        ]
        widgets = {
            "work_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = [
            "expense_date",
            "client",
            "category",
            "amount",
            "description",
            "billable",
            "receipt",
        ]
        widgets = {
            "expense_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

class InvoiceCreateForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ["client", "invoice_number", "issue_date", "due_date", "notes"]
        widgets = {
            "issue_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["invoice_number"].required = False
        self.fields["due_date"].required = False
        # Pre-fill with next auto-generated number, but allow override
        if not self.initial.get("invoice_number"):
            self.initial["invoice_number"] = Invoice._generate_next_invoice_number()

class InvoiceUpdateForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ["invoice_number", "issue_date", "due_date", "notes"]
        widgets = {
            "issue_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["invoice_number"].required = False
        self.fields["due_date"].required = False

class InvoiceLineForm(forms.ModelForm):
    class Meta:
        model = InvoiceLine
        fields = ["line_type", "description", "unit_price"]
        widgets = {
            "description": forms.Textarea(attrs={
                "rows": 1,
                "style": "resize:vertical;"
            }),
        }

    def clean(self):
        cleaned = super().clean()
        cleaned["quantity"] = 1
        return cleaned

    def clean_line_type(self):
        lt = self.cleaned_data["line_type"]
        if lt in (InvoiceLine.LineType.TIME, InvoiceLine.LineType.EXPENSE):
            raise forms.ValidationError("TIME/EXPENSE lines cannot be edited here.")
        return lt
  
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Only allow manual creation of GENERAL or ADJUSTMENT lines
        allowed = [
            InvoiceLine.LineType.GENERAL,
            InvoiceLine.LineType.ADJUSTMENT,
        ]
        self.fields["line_type"].choices = [
            (value, label)
            for value, label in InvoiceLine.LineType.choices
            if value in allowed
        ]
        if self.instance.pk is None:
            self.initial["quantity"] = 1
            self.fields["unit_price"].initial = ""
            self.fields["description"].initial = ""
            self.fields["line_type"].initial = ""

               
# class GeneralAdjustmentLineFormSet(BaseInlineFormSet):

#     def _construct_form(self, i, **kwargs):
#         form = super()._construct_form(i, **kwargs)

#         # Only operate on NEW extra forms
#         if not form.instance.pk and form.is_bound:
#             data = form.data
#             prefix = form.add_prefix

#             desc  = data.get(prefix("description"), "").strip()
#             qty   = data.get(prefix("quantity"), "").strip()
#             price = data.get(prefix("unit_price"), "").strip()

#             # If all meaningful fields are empty → treat this as a blank extra row
#             if desc == "" and qty == "" and price == "":
#                 form.empty_permitted = True

#                 # Mark DELETE so Django removes it silently
#                 mutable = form.data.copy()
#                 mutable[prefix("DELETE")] = "on"
#                 form.data = mutable

#         return form

class GeneralAdjustmentLineFormSet(BaseInlineFormSet):

    def _construct_form(self, i, **kwargs):
        form = super()._construct_form(i, **kwargs)

        # Only apply to NEW blank lines
        if not form.instance.pk and form.is_bound:
            prefix = form.add_prefix
            data = form.data

            desc  = data.get(prefix("description"), "").strip()
            price = data.get(prefix("unit_price"), "").strip()

            # If nothing meaningful entered, make Django treat as empty
            if desc == "" and price == "":
                form.empty_permitted = True

                # IMPORTANT: Pretend DELETE checkbox was checked
                mutable = data.copy()
                mutable[prefix("DELETE")] = "on"
                form.data = mutable

        return form
    
UpdateInvoiceLineFormSet = inlineformset_factory(
    Invoice,
    InvoiceLine,
    form=InvoiceLineForm,
    formset=GeneralAdjustmentLineFormSet,
    extra=1,
    can_delete=True,
    validate_min=False,
    validate_max=False,
)

CreateInvoiceLineFormSet = inlineformset_factory(
    Invoice,
    InvoiceLine,
    form=InvoiceLineForm,
    formset=GeneralAdjustmentLineFormSet,
    extra=1,
    can_delete=False,     # <<< FIX
)
