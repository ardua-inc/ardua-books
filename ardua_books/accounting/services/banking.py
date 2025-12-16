from decimal import Decimal
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from accounting.models import (
    BankAccount,
    BankAccountType,
    BankTransaction,
    ChartOfAccount,
    AccountType,
    JournalEntry,
    JournalLine,
    Payment,
)

from billing.models import Invoice

class BankAccountService:
    """
    Handles coordinated creation of BankAccounts,
    their associated ChartOfAccount records, and opening balance JEs.
    """

    @staticmethod
    def _next_coa_code():
        """
        Generates next COA code in the 1110–1199 range.
        Uses numeric sorting on existing string codes.
        """
        existing = (
            ChartOfAccount.objects
            .filter(code__gte="1110", code__lte="1199")
            .order_by("-code")
            .first()
        )

        if not existing:
            return "1110"

        try:
            n = int(existing.code)
            return str(n + 1)
        except ValueError:
            # Should never happen unless someone manually creates bad codes.
            raise RuntimeError(f"Invalid COA code encountered: {existing.code}")

    @staticmethod
    @transaction.atomic
    def create_bank_account(type, institution, masked, opening_balance):
        """
        Creates:
        1. A new COA asset or liability account
        2. A BankAccount row linked to that COA
        3. A JournalEntry for the opening balance (if nonzero)
        """

        is_credit_card = (type == "CREDIT_CARD")

        if is_credit_card:
            coa_type = AccountType.LIABILITY
        else:
            coa_type = AccountType.ASSET

        # Create COA record
        coa_code = BankAccountService._next_coa_code()
        coa_name = f"{institution} ({masked})"

        coa = ChartOfAccount.objects.create(
            code=coa_code,
            name=coa_name,
            type=coa_type,
            is_active=True,
        )

        # Create BankAccount wrapper
        ba = BankAccount.objects.create(
            account=coa,
            type=type,
            institution=institution,
            account_number_masked=masked,
            opening_balance=opening_balance,
        )

        if Decimal(opening_balance) != 0:
            BankAccountService._create_opening_balance_je(
                bank_account=ba,
                opening_balance=Decimal(opening_balance),
            )

        return ba

    @staticmethod
    def _create_opening_balance_je(bank_account, opening_balance):
        """
        Posts:
        ASSET account:
            positive opening:   Dr Bank / Cr Owner Equity
            negative opening:   Dr Owner Equity / Cr Bank
        LIABILITY account (credit cards):
           positive opening:   Dr Owner Equity / Cr Liability
        """
        owner_equity = ChartOfAccount.objects.get(code="3000")

        je = JournalEntry.objects.create(
            description=f"Opening balance for {bank_account}",
            posted_by=None,
        )

        je.content_type = ContentType.objects.get_for_model(bank_account)
        je.object_id = bank_account.id
        je.save()

        is_asset = (bank_account.account.type == AccountType.ASSET)
        is_liability = (bank_account.account.type == AccountType.LIABILITY)

        if is_asset:
            if opening_balance > 0:
                # Dr Bank
                JournalLine.objects.create(
                    entry=je,
                    account=bank_account.account,
                    debit=opening_balance,
                    credit=0,
                )
                # Cr Equity
                JournalLine.objects.create(
                    entry=je,
                    account=owner_equity,
                    debit=0,
                    credit=opening_balance,
                )
            else:
                ob = abs(opening_balance)
                # Dr Equity
                JournalLine.objects.create(
                    entry=je,
                    account=owner_equity,
                    debit=ob,
                    credit=0,
                )
                # Cr Bank
                JournalLine.objects.create(
                    entry=je,
                    account=bank_account.account,
                    debit=0,
                    credit=ob,
                )

        elif is_liability:
            ob = opening_balance
            # Dr Owner Equity
            JournalLine.objects.create(
                entry=je,
                account=owner_equity,
                debit=ob,
                credit=0,
            )
            # Cr Liability
            JournalLine.objects.create(
                entry=je,
                account=bank_account.account,
                debit=0,
                credit=ob,
            )


class BankTransactionService:
    """
    Centralized logic for:
      • Creating bank transactions
      • Creating opening balance journal entries
      • Posting transactions to accounting
      • Creating payments from transactions
      • Linking bank activity to existing payments
    """

    # ----------------------------------------------------------------------
    # 1. CREATE BANK TRANSACTION
    # ----------------------------------------------------------------------
    @staticmethod
    @transaction.atomic
    def post_transaction(bank_account, date, description, amount, offset_account):
        """
        Creates a BankTransaction AND its associated JournalEntry.

        amount: positive = deposit
                negative = withdrawal / charge
        """
        txn = BankTransaction.objects.create(
            bank_account=bank_account,
            date=date,
            description=description,
            amount=Decimal(amount),
            offset_account=offset_account,
        )

        # Build JE
        je = JournalEntry.objects.create(
            posted_at=date,
            description=f"Bank txn: {description}",
            source_content_type=ContentType.objects.get_for_model(BankTransaction),
            source_object_id=txn.id,
        )

        amt = txn.amount

        if amt > 0:
            # Deposit
            debit_account = bank_account.account
            credit_account = offset_account
            debit = amt
            credit = amt
        else:
            # Withdrawal
            debit_account = offset_account
            credit_account = bank_account.account
            debit = abs(amt)
            credit = abs(amt)

        JournalLine.objects.create(
            entry=je,
            account=debit_account,
            debit=debit,
            credit=Decimal("0"),
        )
        JournalLine.objects.create(
            entry=je,
            account=credit_account,
            debit=Decimal("0"),
            credit=credit,
        )

        txn.journal_entry = je
        txn.save(update_fields=["journal_entry"])

        return txn

    # ----------------------------------------------------------------------
    # 2. CREATE OPENING BALANCE JOURNAL ENTRY
    # ----------------------------------------------------------------------
    @staticmethod
    @transaction.atomic
    def create_opening_balance_entry(bank_account):
        """
        Creates a JE for the bank account's opening balance.
        Only used at account creation time.
        """
        ob = bank_account.opening_balance
        if ob == 0:
            return None

        je = JournalEntry.objects.create(
            posted_at=bank_account.created_at.date(),
            description=f"Opening Balance for {bank_account}",
            source_content_type=ContentType.objects.get_for_model(BankAccount),
            source_object_id=bank_account.id,
        )

        # Debit (increase) bank account asset
        JournalLine.objects.create(
            entry=je,
            account=bank_account.account,
            debit=ob,
            credit=Decimal("0"),
        )

        # Credit Owner Equity
        offset = ChartOfAccount.objects.get(code="3000")  # Owner Equity
        JournalLine.objects.create(
            entry=je,
            account=offset,
            debit=Decimal("0"),
            credit=ob,
        )

        return je

    # ----------------------------------------------------------------------
    # 3. CREATE PAYMENT FROM BANK TRANSACTION
    # ----------------------------------------------------------------------
    @staticmethod
    @transaction.atomic
    def create_payment_from_transaction(txn, client, date, amount, method, memo):
        """
        Creates a Payment and links it to the BankTransaction.
        JE is created by Payment.post_to_accounting().
        """
        payment = Payment.objects.create(
            client=client,
            date=date,
            amount=Decimal(amount),
            method=method,
            memo=memo,
            unapplied_amount=Decimal(amount),
        )

        # Post JE
        je = payment.post_to_accounting()

        # Link to transaction
        txn.payment = payment
        txn.journal_entry = je
        txn.save(update_fields=["payment", "journal_entry"])

        return payment

    # ----------------------------------------------------------------------
    # 4. LINK BANK TRANSACTION TO EXISTING PAYMENT
    # ----------------------------------------------------------------------
    @staticmethod
    @transaction.atomic
    def link_existing_payment(txn, payment):
        """
        Links a bank transaction to a pre-existing payment.

        Only allowed if:
          • txn.payment is None
          • payment.amount == txn.amount

        Also updates the payment date to match the bank transaction date,
        since the bank statement date is authoritative.
        """
        if txn.payment is not None:
            raise ValueError("Bank transaction is already linked to a payment.")

        if Decimal(payment.amount) != Decimal(txn.amount):
            raise ValueError(
                f"Payment (${payment.amount}) and transaction (${txn.amount}) amounts do not match."
            )

        # Update payment date to match bank transaction (authoritative source)
        if payment.date != txn.date:
            payment.date = txn.date
            payment.save(update_fields=["date"])

        je = payment.post_to_accounting()

        txn.payment = payment
        txn.journal_entry = je
        txn.save(update_fields=["payment", "journal_entry"])

        return payment

    # ----------------------------------------------------------------------
    # 5. CHANGE OFFSET ACCOUNT ON EXISTING TRANSACTION
    # ----------------------------------------------------------------------
    @staticmethod
    @transaction.atomic
    def retag_transaction(txn, new_offset_account):
        """
        Retags (recategorizes) a posted bank transaction.
        Requires rebuilding the JournalEntry lines.
        """
        je = txn.journal_entry
        je.lines.all().delete()

        amt = txn.amount

        if amt > 0:
            debit = amt
            credit = amt
            debit_acct = txn.bank_account.account
            credit_acct = new_offset_account
        else:
            debit = abs(amt)
            credit = abs(amt)
            debit_acct = new_offset_account
            credit_acct = txn.bank_account.account

        JournalLine.objects.create(entry=je, account=debit_acct, debit=debit, credit=0)
        JournalLine.objects.create(entry=je, account=credit_acct, debit=0, credit=credit)

        txn.offset_account = new_offset_account
        txn.save(update_fields=["offset_account"])

        return txn