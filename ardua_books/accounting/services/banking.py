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

    # ----------------------------------------------------------------------
    # 6. LINK BANK TRANSACTION TO EXPENSE
    # ----------------------------------------------------------------------
    @staticmethod
    @transaction.atomic
    def link_expense(txn, expense):
        """
        Links a bank transaction to an expense and posts to GL.

        Creates a journal entry:
            DR Expense Account (from expense.category.account)
            CR Bank Account (from txn.bank_account.account)

        Also updates expense.payment_account to the bank account.
        """
        from billing.models import Expense

        if txn.expense is not None:
            raise ValueError("Bank transaction is already linked to an expense.")

        if txn.payment is not None:
            raise ValueError("Bank transaction is already linked to a payment.")

        expense_account = expense.category.account
        if not expense_account:
            raise ValueError(
                f"Expense category '{expense.category.name}' has no GL account assigned. "
                "Please assign a GL account to this category first."
            )

        # Delete the original JE from post_transaction() to avoid double-posting
        if txn.journal_entry:
            txn.journal_entry.delete()
            txn.journal_entry = None

        # Update expense with payment account
        expense.payment_account = txn.bank_account
        expense.save(update_fields=["payment_account"])

        # Create Journal Entry
        amt = abs(txn.amount)
        je = JournalEntry.objects.create(
            posted_at=txn.date,
            description=f"Expense: {expense.description or expense.category.name}",
            source_content_type=ContentType.objects.get_for_model(Expense),
            source_object_id=expense.id,
        )

        # DR Expense Account
        JournalLine.objects.create(
            entry=je,
            account=expense_account,
            debit=amt,
            credit=Decimal("0"),
        )

        # CR Bank Account
        JournalLine.objects.create(
            entry=je,
            account=txn.bank_account.account,
            debit=Decimal("0"),
            credit=amt,
        )

        # Link transaction to expense
        txn.expense = expense
        txn.offset_account = expense_account
        txn.journal_entry = je
        txn.save(update_fields=["expense", "offset_account", "journal_entry"])

        return expense

    # ----------------------------------------------------------------------
    # 7. MATCH INTER-ACCOUNT TRANSFER
    # ----------------------------------------------------------------------
    @staticmethod
    @transaction.atomic
    def match_transfer(txn_from, txn_to):
        """
        Matches two bank transactions as an inter-account transfer.

        For example: paying a credit card from checking:
        - txn_from: checking withdrawal (-$500)
        - txn_to: credit card payment (+$500)

        Creates a single journal entry:
            DR destination account (liability/asset being paid)
            CR source account (asset/liability paying)

        Both transactions share the same JE and are linked via transfer_pair.
        """
        # Validation: neither should already be matched
        if txn_from.is_matched:
            raise ValueError("Source transaction is already matched to a payment, expense, or transfer.")
        if txn_to.is_matched:
            raise ValueError("Destination transaction is already matched to a payment, expense, or transfer.")

        # Must be different accounts
        if txn_from.bank_account_id == txn_to.bank_account_id:
            raise ValueError("Cannot match a transfer between the same account.")

        # Amounts should be opposite (or at least same absolute value)
        if abs(txn_from.amount) != abs(txn_to.amount):
            raise ValueError(
                f"Transaction amounts don't match: ${abs(txn_from.amount)} vs ${abs(txn_to.amount)}"
            )

        # Delete original JEs from post_transaction() to avoid double-posting
        if txn_from.journal_entry:
            txn_from.journal_entry.delete()
            txn_from.journal_entry = None
        if txn_to.journal_entry:
            txn_to.journal_entry.delete()
            txn_to.journal_entry = None

        # Determine which is the source (withdrawal) and destination (deposit)
        # Source should be negative (money leaving), destination positive (money arriving)
        # But for credit card payments, the CC side shows positive (reducing liability)
        # Let's just use the accounts: the one with negative amount is the source
        if txn_from.amount < 0:
            source_txn = txn_from
            dest_txn = txn_to
        else:
            source_txn = txn_to
            dest_txn = txn_from

        source_account = source_txn.bank_account.account
        dest_account = dest_txn.bank_account.account
        amt = abs(source_txn.amount)

        # Create journal entry for the transfer
        je = JournalEntry.objects.create(
            posted_at=source_txn.date,
            description=f"Transfer: {source_txn.bank_account.institution} → {dest_txn.bank_account.institution}",
            source_content_type=ContentType.objects.get_for_model(BankTransaction),
            source_object_id=source_txn.id,
        )

        # DR destination (receiving) account
        JournalLine.objects.create(
            entry=je,
            account=dest_account,
            debit=amt,
            credit=Decimal("0"),
        )

        # CR source (paying) account
        JournalLine.objects.create(
            entry=je,
            account=source_account,
            debit=Decimal("0"),
            credit=amt,
        )

        # Link both transactions to the JE and each other
        txn_from.journal_entry = je
        txn_from.transfer_pair = txn_to
        txn_from.offset_account = txn_to.bank_account.account
        txn_from.save(update_fields=["journal_entry", "transfer_pair", "offset_account"])

        txn_to.journal_entry = je
        txn_to.transfer_pair = txn_from
        txn_to.offset_account = txn_from.bank_account.account
        txn_to.save(update_fields=["journal_entry", "transfer_pair", "offset_account"])

        return je