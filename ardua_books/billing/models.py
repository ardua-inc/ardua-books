from django.conf import settings
from django.db import models
from django.utils import timezone
from django.urls import reverse
from django.core.exceptions import ValidationError


class TimeStampedModel(models.Model):
    """Abstract base for created/updated timestamps."""
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Client(TimeStampedModel):
    name = models.CharField(max_length=255, unique=True)
    billing_address = models.TextField(blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)

    # Default hourly rate for time entries for this client
    default_hourly_rate = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )

    # e.g. 30 = Net 30
    payment_terms_days = models.PositiveIntegerField(default=30)

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Consultant(TimeStampedModel):
    """
    For Stage 1, you can have a single Consultant linked to your Django user.
    Later, multiple consultants/users can be added.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )
    display_name = models.CharField(max_length=255)

    # Optional consultant-specific default rate; if null, use client default
    default_hourly_rate = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )

    def __str__(self):
        return self.display_name


class ExpenseCategory(TimeStampedModel):
    """
    Categories like Lodging, Airfare, Meals, Software, etc.
    """
    name = models.CharField(max_length=100, unique=True)
    billable_by_default = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class BillableStatus(models.TextChoices):
    UNBILLED = "UNBILLED", "Unbilled"
    BILLED = "BILLED", "Billed"
    WRITTEN_OFF = "WRITTEN_OFF", "Written off"


class TimeEntry(TimeStampedModel):
    client = models.ForeignKey(Client, on_delete=models.PROTECT)
    consultant = models.ForeignKey(Consultant, on_delete=models.PROTECT)

    work_date = models.DateField(default=timezone.now)
    hours = models.DecimalField(max_digits=5, decimal_places=2)  # e.g. 999.99 max
    description = models.TextField()

    # Rate actually used for this entry (copied from client/consultant at entry time)
    billing_rate = models.DecimalField(max_digits=8, decimal_places=2)

    status = models.CharField(
        max_length=20,
        choices=BillableStatus.choices,
        default=BillableStatus.UNBILLED,
    )

    # Once billed, this can point back to the line that used it
    # (optional; helpful later for audit reports)
    # Use string reference because InvoiceLine is defined below.
    invoice_line = models.OneToOneField(
        "InvoiceLine",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="time_entry",
    )

    class Meta:
        ordering = ["-work_date", "-created_at"]

    def __str__(self):
        return f"{self.work_date} {self.client} {self.hours}"


class Expense(TimeStampedModel):
    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Client, if this is a billable expense.",
    )
    category = models.ForeignKey(
        ExpenseCategory, on_delete=models.PROTECT
    )

    expense_date = models.DateField(default=timezone.now)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField(blank=True)

    billable = models.BooleanField(default=True)

    status = models.CharField(
        max_length=20,
        choices=BillableStatus.choices,
        default=BillableStatus.UNBILLED,
    )

    receipt = models.FileField(
        upload_to="receipts/%Y/%m/%d", blank=True, null=True
    )

    invoice_line = models.OneToOneField(
        "InvoiceLine",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expense",
    )

    class Meta:
        ordering = ["-expense_date", "-created_at"]

    def __str__(self):
        client_name = self.client.name if self.client else "No client"
        return f"{self.expense_date} {client_name} {self.amount}"


class InvoiceStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ISSUED = "ISSUED", "Issued"
    PAID = "PAID", "Paid"
    VOID = "VOID", "Void"


class Invoice(TimeStampedModel):
    def save(self, *args, **kwargs):
        self.full_clean()
        # Auto-numbering only if invoice_number is blank
        if not self.invoice_number:
            last = Invoice.objects.order_by("-sequence").first()
            next_seq = (last.sequence + 1) if last else 1
            self.sequence = next_seq
            self.invoice_number = f"{next_seq:05d}"
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()

        if self.status == InvoiceStatus.DRAFT:
            qs = Invoice.objects.filter(client=self.client, status=InvoiceStatus.DRAFT)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError(
                    "This client already has an invoice in Draft status. "
                    "Please issue or delete the existing draft before creating a new one."
                )

    def get_absolute_url(self):
        return reverse("billing:invoice_detail", args=[self.pk])

    def applied_payments_total(self):
        return sum(app.amount for app in self.paymentapplication_set.all())

    def outstanding_balance(self):
        return self.total - self.applied_payments_total()

    def is_paid(self):
        return self.outstanding_balance() <= 0
    
    def update_status(self):
        if self.status == InvoiceStatus.DRAFT:
            return
        bal = self.outstanding_balance()
        if bal <=0:
            if self.status != InvoiceStatus.PAID:
                self.status = InvoiceStatus.PAID
                self.save(update_fields=["status"])

    client = models.ForeignKey(Client, on_delete=models.PROTECT)

    invoice_number = models.CharField(
        max_length=50, unique=True, help_text="e.g. 2025-001", blank=True,
    )
    sequence = models.PositiveIntegerField(default=0, editable=False)

    issue_date = models.DateField(default=timezone.now)
    due_date = models.DateField()

    status = models.CharField(
        max_length=10,
        choices=InvoiceStatus.choices,
        default=InvoiceStatus.DRAFT,
    )

    # Optional freeform notes printed on invoice
    notes = models.TextField(blank=True)

    # Cached totals for performance; recomputed whenever lines change
    subtotal = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )
    tax_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )
    total = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )

    def __str__(self):
        return f"Invoice {self.invoice_number} - {self.client.name}"

    @property
    def other_draft_exists(self):
        return (
            Invoice.objects
            .filter(client=self.client, status=InvoiceStatus.DRAFT)
            .exclude(pk=self.pk)
            .exists()
        )
    
    def recalculate_totals(self):
        subtotal = sum(
            line.line_total for line in self.lines.all()
        )
        self.subtotal = subtotal
        # For Stage 1, assume no tax (or apply a simple flat rate if you like)
        self.tax_amount = 0
        self.total = subtotal
        self.save(update_fields=["subtotal", "tax_amount", "total"])


class InvoiceLine(TimeStampedModel):
    class LineType(models.TextChoices):
        TIME = "TIME", "Time entry"
        EXPENSE = "EXPENSE", "Expense"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"
        GENERAL = "GENERAL", "General line"

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="lines",
    )

    line_type = models.CharField(
        max_length=20, choices=LineType.choices
    )

    description = models.TextField()

    quantity = models.DecimalField(
        max_digits=8, decimal_places=2, default=1
    )  # hours for time, units for expenses
    unit_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )
    line_total = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )

    # FK back to specific TimeEntry / Expense are on those models
    # via their 'invoice_line' OneToOne fields.

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.invoice.invoice_number}: {self.description}"

    def save(self, *args, **kwargs):
        # Always compute line_total
        self.line_total = (self.quantity or 0) * (self.unit_price or 0)
        super().save(*args, **kwargs)
