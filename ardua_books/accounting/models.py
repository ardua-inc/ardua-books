from decimal import Decimal
from django.db import models, transaction
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from accounting import gl_codes
from billing.models import Client, Invoice

User = get_user_model()


# ============================================================
# Chart of Accounts
# ============================================================

class AccountType(models.TextChoices):
    ASSET = "ASSET", "Asset"
    LIABILITY = "LIABILITY", "Liability"
    EQUITY = "EQUITY", "Equity"
    INCOME = "INCOME", "Income"
    EXPENSE = "EXPENSE", "Expense"


class ChartOfAccount(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=20, choices=AccountType.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["type", "code"]

    def __str__(self):
        return f"{self.code} {self.name}"


# ============================================================
# Journal Entry
# ============================================================

class JournalEntry(models.Model):
    posted_at = models.DateTimeField(default=timezone.now)
    posted_by = models.ForeignKey(User, on_delete=models.SET_NULL,
                                  null=True, blank=True)
    description = models.CharField(max_length=255, blank=True)

    # Generic relation to originating business document
    source_content_type = models.ForeignKey(
        ContentType, on_delete=models.SET_NULL, null=True, blank=True
    )
    source_object_id = models.PositiveIntegerField(null=True, blank=True)
    source_object = GenericForeignKey("source_content_type", "source_object_id")

    # Entered by hand (accounting.services.journal), not posted by a payment,
    # invoice, expense or bank transaction. A reversal is also manual, with the
    # entry it reverses as its source.
    is_manual = models.BooleanField(default=False)

    def __str__(self):
        return f"JE #{self.id} ({self.posted_at.date()})"

    @property
    def reverses(self):
        """The entry this one reverses, or None."""
        if self.is_manual and self.source_content_type_id == ContentType.objects.get_for_model(JournalEntry).id:
            return JournalEntry.objects.filter(pk=self.source_object_id).first()
        return None

    @property
    def reversed_by(self):
        """The reversal of this entry, or None."""
        return JournalEntry.objects.filter(
            is_manual=True,
            source_content_type=ContentType.objects.get_for_model(JournalEntry),
            source_object_id=self.id,
        ).first()


class JournalLine(models.Model):
    entry = models.ForeignKey(
        JournalEntry, related_name="lines", on_delete=models.CASCADE
    )
    account = models.ForeignKey(ChartOfAccount, on_delete=models.PROTECT)

    debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.account} DR {self.debit} CR {self.credit}"


# ============================================================
# Payment Models
# ============================================================

class PaymentMethod(models.TextChoices):
    CHECK = "check", "Check"
    ACH = "ach", "ACH Transfer"
    CASH = "cash", "Cash"
    CARD = "card", "Credit Card"
    OTHER = "other", "Other"


class Payment(models.Model):
    client = models.ForeignKey(Client, on_delete=models.PROTECT)
    date = models.DateField(default=timezone.now)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    memo = models.CharField(max_length=255, blank=True)
    unapplied_amount = models.DecimalField(max_digits=10, decimal_places=2,
                                           default=0)

    def __str__(self):
        return f"Payment {self.id} ({self.client.name})"

    # --------------------------------------------------------
    # Accounting posting for payments
    # --------------------------------------------------------
    @transaction.atomic
    def post_to_accounting(self, user=None, deposit_account=None):
        """
        Creates a JournalEntry for this payment.

        DR deposit_account, else Cash (full payment amount)
            CR Accounts Receivable (sum of applications)
            CR Payment Clearing / Unapplied Payments (remaining unapplied)

        deposit_account is the GL account of the bank deposit the payment is
        linked to. A payment recorded before (or without) its deposit debits
        Cash, which acts as undeposited funds; linking it to the deposit later
        moves that debit (BankTransactionService.link_existing_payment).
        """
        # Prevent duplicate journal entries
        existing = JournalEntry.objects.filter(
            source_content_type=ContentType.objects.get_for_model(Payment),
            source_object_id=self.id,
        ).first()
        if existing:
            return existing

        # Required accounts
        cash_acct = deposit_account or gl_codes.gl_account(gl_codes.CASH)
        ar_acct = gl_codes.gl_account(gl_codes.ACCOUNTS_RECEIVABLE)  # same account invoices debit
        clearing_acct = gl_codes.gl_account(gl_codes.UNAPPLIED_PAYMENTS)  # Liability for unapplied

        # Amounts
        payment_total = Decimal(self.amount)
        applied_total = sum(app.amount for app in self.applications.all())
        unapplied = payment_total - applied_total

        # Create Journal Entry
        je = JournalEntry.objects.create(
            posted_at=self.date,
            posted_by=user,
            description=f"Payment received from {self.client.name}",
            source_content_type=ContentType.objects.get_for_model(Payment),
            source_object_id=self.id,
        )

        # DR Cash (or Undeposited Funds)
        JournalLine.objects.create(
            entry=je,
            account=cash_acct,
            debit=payment_total,
            credit=Decimal("0"),
        )

        # CR Accounts Receivable (only for applied amounts)
        if applied_total > 0:
            JournalLine.objects.create(
                entry=je,
                account=ar_acct,
                debit=Decimal("0"),
                credit=applied_total,
            )

        # CR Clearing for any unapplied amount
        if unapplied > 0:
            JournalLine.objects.create(
                entry=je,
                account=clearing_acct,
                debit=Decimal("0"),
                credit=unapplied,
            )

        return je


class PaymentApplication(models.Model):
    payment = models.ForeignKey(
        Payment,
        on_delete=models.CASCADE,
        related_name="applications"
    )
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"PaymentApp Payment={self.payment_id} Invoice={self.invoice_id} Amount={self.amount}"

# ---------------------------------------------------------
# Bank Accounts & Transactions
# ---------------------------------------------------------

class BankAccountType(models.TextChoices):
    CHECKING = "CHECKING", "Checking"
    SAVINGS = "SAVINGS", "Savings"
    CREDIT_CARD = "CREDIT_CARD", "Credit Card"
    CASH = "CASH", "Cash"


class BankAccount(models.Model):
    """
    Represents a real-world bank or credit-card account.
    It wraps a GL ChartOfAccount entry (auto-created).
    """
    account = models.OneToOneField(
        ChartOfAccount,
        on_delete=models.PROTECT,
        related_name="bank_account",
    )

    type = models.CharField(
        max_length=20,
        choices=BankAccountType.choices,
    )

    institution = models.CharField(max_length=255)
    account_number_masked = models.CharField(max_length=20)

    opening_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Initial balance at system inception."
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["institution"]

    def __str__(self):
        return f"{self.institution} ({self.account_number_masked})"

    @property
    def balance(self):
        """
        Computes the account balance from BankTransaction records.
        This is the source of truth for bank account balances.

        Balance = opening_balance + sum of all transaction amounts
        """
        from django.db.models import Sum

        txn_sum = (
            self.transactions.aggregate(s=Sum("amount"))["s"]
            or Decimal("0")
        )
        return (self.opening_balance or Decimal("0")) + txn_sum

    
class BankTransaction(models.Model):
    """
    Represents a deposit, withdrawal, or charge against a BankAccount.
    Posting always generates a JournalEntry.
    """

    bank_account = models.ForeignKey(
        BankAccount,
        on_delete=models.CASCADE,
        related_name="transactions",
    )

    date = models.DateField()
    description = models.CharField(max_length=255)

    # Positive = deposit/add funds
    # Negative = withdrawal / charge / payment
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    # The JE created for this transaction
    # ForeignKey (not OneToOne) because transfers share a JE between two transactions
    journal_entry = models.ForeignKey(
        "accounting.JournalEntry",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bank_transactions",
    )

    offset_account = models.ForeignKey(
        ChartOfAccount,
        on_delete=models.PROTECT,
        related_name="bank_transactions_offset",
        null=True,
    )

    expense = models.ForeignKey(
        "billing.Expense",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bank_transactions",
    )

    payment = models.ForeignKey(
        "accounting.Payment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bank_transactions",
        help_text="If this bank transaction has been matched to a payment, it appears here.",
    )

    # For inter-account transfers, links the two matching transactions
    transfer_pair = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transfer_linked",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        amt = f"+{self.amount}" if self.amount > 0 else f"{self.amount}"
        return f"{self.date} {amt} — {self.description}"

    @property
    def is_matched(self):
        """Returns True if this transaction is matched to a payment, expense, or transfer."""
        return bool(self.payment_id or self.expense_id or self.transfer_pair_id)

    
class BankImportProfile(models.Model):
    bank_account = models.OneToOneField(
        BankAccount,
        on_delete=models.CASCADE,
        related_name="import_profile",
    )

    # Column positions (0-based index)
    date_column_index = models.PositiveSmallIntegerField()
    description_column_index = models.PositiveSmallIntegerField()
    amount_column_index = models.PositiveSmallIntegerField()

    # Date parsing
    date_format = models.CharField(
        max_length=50,
        default="%Y-%m-%d",
        help_text="Python strptime format, e.g. %m/%d/%Y",
    )

    SIGN_RULE_CHOICES = [
        ("BANK_STANDARD", "Bank: + deposit, - withdrawal"),
        ("CC_CHARGES_POSITIVE", "Credit card: + charge, - payment (Amex)"),
        ("CC_CHARGES_NEGATIVE", "Credit card: - charge, + payment (Chase)"),
    ]

    sign_rule = models.CharField(
        max_length=30,
        choices=SIGN_RULE_CHOICES,
        blank=True,
        help_text="For bank accounts this is auto-assigned.",
    )

    skip_if_description_contains = models.CharField(
        max_length=200,
        blank=True,
        help_text="If set, skip rows whose description contains this phrase.",
    )

    def save(self, *args, **kwargs):
        # Auto-assign correct default for bank accounts
        if not self.sign_rule:
            if self.bank_account.type in (
                BankAccountType.CHECKING,
                BankAccountType.SAVINGS,
            ):
                self.sign_rule = "BANK_STANDARD"
            else:
                raise ValueError(
                    "Credit card import profiles must specify a sign rule "
                    "(CC_CHARGES_POSITIVE or CC_CHARGES_NEGATIVE)."
                )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Import profile for {self.bank_account}"


# ============================================================
# Transaction Categorization Rules
# ============================================================

class TransactionRule(models.Model):
    """
    Matches unmatched bank transactions by description substring and suggests
    an expense category in the batch-match UI. Higher priority rules are checked first.
    """
    name = models.CharField(max_length=100)
    description_contains = models.CharField(
        max_length=200,
        help_text="Case-insensitive substring to match against transaction description.",
    )
    bank_account = models.ForeignKey(
        BankAccount,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transaction_rules",
        help_text="Leave blank to match transactions from any bank account.",
    )
    category = models.ForeignKey(
        "billing.ExpenseCategory",
        on_delete=models.PROTECT,
        related_name="transaction_rules",
    )
    priority = models.IntegerField(
        default=0,
        help_text="Higher values are evaluated first.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-priority", "name"]

    def __str__(self):
        scope = f" [{self.bank_account}]" if self.bank_account_id else ""
        return f"{self.name}{scope} → {self.category.name}"
