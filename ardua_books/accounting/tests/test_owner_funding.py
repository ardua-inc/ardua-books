"""
Tests for 3.3.6: recording what doesn't pass through a business bank or card
account, and correcting matches.

A. Accounts 2500 Loan from Shareholder, 6110 Rent, 6120 Vehicle Expenses,
   6130 Repairs & Maintenance and their categories (data migration).
B. Recategorize an unmatched bank transaction to any non-bank account, one at
   a time or in bulk.
C. Expenses funded outside the business accounts (Expense.funding_account).
D. Manual journal entries, and reversing them.
E. Applying a payment's unapplied balance to an invoice.
F. Unmatching an expense link or a transfer; deleting a matched expense.

Every test ends with assert_books_consistent(): each register equals its GL
account and every journal entry balances.
"""

import importlib
from datetime import date
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.urls import reverse

from accounting import gl_codes
from accounting.forms import BankTransactionLinkExpenseForm
from accounting.models import (
    AccountType,
    BankTransaction,
    ChartOfAccount,
    JournalEntry,
    Payment,
    PaymentApplication,
    PaymentMethod,
)
from accounting.services.banking import BankTransactionService
from accounting.services.expenses import ExpenseLedgerService
from accounting.services.journal import JournalEntryService
from accounting.services.payments import PaymentService
from accounting.tests.helpers import (
    assert_books_consistent,
    balance,
    code_balance,
    deposit,
    entry_date,
    issued_invoice,
    lines,
    message_texts,
    posted_against,
    register_equals_gl,
    txn_entries,
)
from billing.models import BillableStatus, Expense, ExpenseCategory, InvoiceStatus
from conftest import ChartOfAccountFactory, ClientFactory, ExpenseCategoryFactory, UserFactory

D = Decimal


@pytest.fixture
def loan(gl):
    """2500 Loan from Shareholder (added by the 3.3.6 migration)."""
    return gl_codes.gl_account(gl_codes.SHAREHOLDER_LOAN)


@pytest.fixture
def rent(gl):
    return gl_codes.gl_account(gl_codes.RENT)


@pytest.fixture
def viewer(client, db):
    user = UserFactory()
    user.groups.add(Group.objects.get_or_create(name="Viewer")[0])
    client.force_login(user)
    return client


def owner_equity():
    return gl_codes.gl_account(gl_codes.OWNER_EQUITY)


def make_expense(category, amount, funding_account=None, when=date(2026, 3, 5), **kwargs):
    defaults = dict(client=None, billable=False, description="Purchase")
    defaults.update(kwargs)
    return Expense.objects.create(
        category=category, expense_date=when, amount=D(amount), funding_account=funding_account, **defaults,
    )


def expense_entries(expense):
    return JournalEntry.objects.filter(
        source_content_type=ContentType.objects.get_for_model(Expense), source_object_id=expense.id,
    )


def charge_linked_to_expense(card, category, amount="-40.00"):
    txn = deposit(card, amount, "STORE")
    expense = make_expense(category, -D(amount))
    BankTransactionService.link_expense(txn, expense)
    txn.refresh_from_db()
    return txn, expense


# --- A. chart of accounts ---------------------------------------------------------

def run_owner_funding_migration():
    module = importlib.import_module("billing.migrations.0007_owner_funding")
    module.add_owner_funding_accounts(django_apps, None)


class TestChartOfAccountsMigration:
    def test_accounts_and_categories_exist(self, db):
        expected = {
            gl_codes.SHAREHOLDER_LOAN: ("Loan from Shareholder", AccountType.LIABILITY),
            gl_codes.RENT: ("Rent", AccountType.EXPENSE),
            gl_codes.VEHICLE: ("Vehicle Expenses", AccountType.EXPENSE),
            gl_codes.REPAIRS: ("Repairs & Maintenance", AccountType.EXPENSE),
        }
        for code, (name, kind) in expected.items():
            account = ChartOfAccount.objects.get(code=code)
            assert (account.name, account.type, account.is_active) == (name, kind, True)
        for name, code in [("Rent", gl_codes.RENT), ("Vehicle Expenses", gl_codes.VEHICLE),
                           ("Repairs & Maintenance", gl_codes.REPAIRS)]:
            category = ExpenseCategory.objects.get(name=name)
            assert category.account.code == code
            assert category.billable_by_default is False

    def test_rerun_keeps_existing_rows_and_fills_a_missing_category_account(self, db):
        loan = ChartOfAccount.objects.get(code=gl_codes.SHAREHOLDER_LOAN)
        loan.name = "Shareholder loan (Jim)"
        loan.save()
        ExpenseCategory.objects.filter(name="Rent").update(account=None)
        counts = (ChartOfAccount.objects.count(), ExpenseCategory.objects.count())

        run_owner_funding_migration()

        assert ChartOfAccount.objects.get(code=gl_codes.SHAREHOLDER_LOAN).name == "Shareholder loan (Jim)"
        assert ExpenseCategory.objects.get(name="Rent").account.code == gl_codes.RENT
        assert (ChartOfAccount.objects.count(), ExpenseCategory.objects.count()) == counts


# --- B. recategorize ------------------------------------------------------------------

class TestRecategorize:
    def test_deposit_from_the_owner_becomes_a_shareholder_loan(self, checking, loan):
        txn = deposit(checking, "5000.00", "LOAN TO ARDUA")

        BankTransactionService.retag_transaction(txn, loan)

        txn.refresh_from_db()
        assert txn.offset_account == loan
        assert lines(txn.journal_entry) == sorted([
            (checking.account.code, D("5000.00"), D("0")),
            (gl_codes.SHAREHOLDER_LOAN, D("0"), D("5000.00")),
        ])
        assert balance(loan) == D("-5000.00")
        assert code_balance(gl_codes.OWNER_EQUITY) == D("0")
        assert_books_consistent()

    def test_withdrawal_becomes_rent(self, checking, rent):
        txn = deposit(checking, "-1200.00", "RENT TO JIM")

        BankTransactionService.retag_transaction(txn, rent)

        txn.refresh_from_db()
        assert lines(txn.journal_entry) == sorted([
            (gl_codes.RENT, D("1200.00"), D("0")),
            (checking.account.code, D("0"), D("1200.00")),
        ])
        assert_books_consistent()

    def test_matched_transaction_is_refused(self, card, travel, loan):
        txn, _ = charge_linked_to_expense(card, travel)
        before = lines(txn.journal_entry)

        with pytest.raises(ValueError, match="matched"):
            BankTransactionService.retag_transaction(txn, loan)

        txn.refresh_from_db()
        assert lines(txn.journal_entry) == before
        assert_books_consistent()

    def test_another_bank_account_is_refused(self, checking, card):
        txn = deposit(checking, "100.00")

        with pytest.raises(ValueError, match="Match Transfer"):
            BankTransactionService.retag_transaction(txn, card.account)

        txn.refresh_from_db()
        assert txn.offset_account == owner_equity()
        assert_books_consistent()

    def test_bulk_recategorizes_every_selected_transaction(self, checking, loan):
        txns = [deposit(checking, "1000.00", "LOAN TO ARDUA"), deposit(checking, "250.00", "WEALTHFRONT")]

        BankTransactionService.retag_transactions(checking, txns, loan)

        assert all(BankTransaction.objects.get(pk=t.pk).offset_account == loan for t in txns)
        assert balance(loan) == D("-1250.00")
        assert_books_consistent()

    def test_bulk_is_all_or_none(self, checking, card, travel, loan):
        unmatched = deposit(checking, "1000.00", "LOAN TO ARDUA")
        matched, _ = charge_linked_to_expense(card, travel)

        with pytest.raises(ValueError):
            BankTransactionService.retag_transactions(checking, [unmatched, matched], loan)

        unmatched.refresh_from_db()
        assert unmatched.offset_account == owner_equity()
        assert balance(loan) == D("0")
        assert_books_consistent()

    def test_bulk_refuses_a_transaction_from_another_account(self, checking, card, loan):
        mine = deposit(checking, "10.00")
        theirs = deposit(card, "10.00")

        with pytest.raises(ValueError, match="is not in"):
            BankTransactionService.retag_transactions(checking, [mine, theirs], loan)

        assert BankTransaction.objects.get(pk=mine.pk).offset_account == owner_equity()
        assert_books_consistent()


class TestRecategorizeViews:
    def test_single_view(self, logged_in, checking, loan):
        txn = deposit(checking, "5000.00", "LOAN TO ARDUA")
        url = reverse("accounting:banktransaction_recategorize", args=[txn.pk])

        page = logged_in.get(url)
        assert page.status_code == 200
        choices = page.context["form"].fields["offset_account"].queryset
        assert loan in choices and checking.account not in choices

        response = logged_in.post(url, {"offset_account": loan.pk}, follow=True)
        txn.refresh_from_db()
        assert txn.offset_account == loan
        assert "Transaction recategorized to 2500 Loan from Shareholder." in message_texts(response)
        assert_books_consistent()

    def test_single_view_rejects_a_bank_account(self, logged_in, checking, card):
        txn = deposit(checking, "100.00")
        response = logged_in.post(
            reverse("accounting:banktransaction_recategorize", args=[txn.pk]), {"offset_account": card.account.pk}
        )
        assert response.status_code == 200
        assert response.context["form"].errors
        txn.refresh_from_db()
        assert txn.offset_account == owner_equity()

    def test_owner_equity_shortcut_still_works(self, logged_in, checking, loan):
        txn = posted_against(checking, "75.00", loan)
        logged_in.post(reverse("accounting:banktransaction_mark_owner_equity", args=[txn.pk]))
        txn.refresh_from_db()
        assert txn.offset_account == owner_equity()
        assert_books_consistent()

    def test_bulk_view(self, logged_in, checking, loan):
        txns = [deposit(checking, "1000.00", "LOAN TO ARDUA"), deposit(checking, "250.00", "LOAN TO ARDUA")]
        response = logged_in.post(
            reverse("accounting:banktransaction_bulk_recategorize", args=[checking.pk]),
            {"offset_account": loan.pk, "txn_ids": [t.pk for t in txns]},
            follow=True,
        )
        assert "Recategorized 2 transaction(s) to 2500 Loan from Shareholder." in message_texts(response)
        assert balance(loan) == D("-1250.00")
        assert_books_consistent()

    def test_bulk_view_reports_a_refusal_and_changes_nothing(self, logged_in, checking, travel, loan):
        unmatched = deposit(checking, "1000.00")
        matched, _ = charge_linked_to_expense(checking, travel)
        response = logged_in.post(
            reverse("accounting:banktransaction_bulk_recategorize", args=[checking.pk]),
            {"offset_account": loan.pk, "txn_ids": [unmatched.pk, matched.pk]},
            follow=True,
        )
        assert any(m.startswith("Nothing was recategorized.") for m in message_texts(response))
        assert balance(loan) == D("0")
        assert_books_consistent()

    def test_register_search_filters_by_description(self, logged_in, checking):
        deposit(checking, "1000.00", "ONLINE TRANSFER LOAN TO ARDUA", when=date(2026, 1, 5))
        deposit(checking, "20.00", "INTEREST", when=date(2026, 1, 6))
        response = logged_in.get(
            reverse("accounting:bankaccount_register", args=[checking.pk]), {"q": "loan to", "date_preset": "all"}
        )
        assert [t.description for t in response.context["transactions"]] == ["ONLINE TRANSFER LOAN TO ARDUA"]

    def test_viewer_cannot_recategorize(self, viewer, checking, loan):
        txn = deposit(checking, "100.00")
        url = reverse("accounting:banktransaction_recategorize", args=[txn.pk])
        assert viewer.post(url, {"offset_account": loan.pk}).status_code == 403
        bulk = reverse("accounting:banktransaction_bulk_recategorize", args=[checking.pk])
        assert viewer.post(bulk, {"offset_account": loan.pk, "txn_ids": [txn.pk]}).status_code == 403
        txn.refresh_from_db()
        assert txn.offset_account == owner_equity()


# --- C. funded expenses ---------------------------------------------------------------

class TestFundedExpense:
    def test_funded_expense_posts_expense_against_the_funding_account(self, travel, loan):
        expense = make_expense(travel, "69.43", loan)

        expense.refresh_from_db()
        je = expense.funding_entry
        assert je is not None
        assert list(expense_entries(expense)) == [je]
        assert entry_date(je) == date(2026, 3, 5)
        assert lines(je) == sorted([
            (travel.account.code, D("69.43"), D("0")),
            (gl_codes.SHAREHOLDER_LOAN, D("0"), D("69.43")),
        ])
        assert_books_consistent()

    def test_funded_refund_posts_the_other_way(self, travel, loan):
        expense = make_expense(travel, "-15.00", loan)
        expense.refresh_from_db()
        assert lines(expense.funding_entry) == sorted([
            (gl_codes.SHAREHOLDER_LOAN, D("15.00"), D("0")),
            (travel.account.code, D("0"), D("15.00")),
        ])
        assert_books_consistent()

    def test_unfunded_expense_posts_nothing(self, travel):
        expense = make_expense(travel, "10.00")
        assert expense.funding_entry is None
        assert not expense_entries(expense).exists()

    def test_editing_rebuilds_the_same_entry(self, travel, loan, gl):
        expense = make_expense(travel, "69.43", loan)
        expense.refresh_from_db()
        je_id = expense.funding_entry_id
        other = ExpenseCategoryFactory(name="Supplies", account=ChartOfAccountFactory(type=AccountType.EXPENSE))

        expense.amount = D("70.00")
        expense.expense_date = date(2026, 3, 9)
        expense.category = other
        expense.funding_account = owner_equity()
        expense.save()

        expense.refresh_from_db()
        assert expense.funding_entry_id == je_id
        assert entry_date(expense.funding_entry) == date(2026, 3, 9)
        assert lines(expense.funding_entry) == sorted([
            (other.account.code, D("70.00"), D("0")),
            (gl_codes.OWNER_EQUITY, D("0"), D("70.00")),
        ])
        assert balance(travel.account) == D("0")
        assert_books_consistent()

    def test_a_save_that_does_not_touch_the_posting_leaves_the_entry(self, travel, loan):
        expense = make_expense(travel, "69.43", loan)
        expense.refresh_from_db()
        je_id = expense.funding_entry_id
        expense.status = BillableStatus.BILLED
        expense.save(update_fields=["status"])
        assert JournalEntry.objects.filter(pk=je_id).exists()
        assert expense_entries(expense).count() == 1

    def test_clearing_the_funding_account_deletes_the_entry(self, travel, loan):
        expense = make_expense(travel, "69.43", loan)
        expense.refresh_from_db()
        je_id = expense.funding_entry_id

        expense.funding_account = None
        expense.save()

        expense.refresh_from_db()
        assert expense.funding_entry is None
        assert not JournalEntry.objects.filter(pk=je_id).exists()
        assert balance(loan) == D("0")
        assert_books_consistent()

    def test_deleting_the_expense_deletes_the_entry(self, travel, loan):
        expense = make_expense(travel, "69.43", loan)
        expense.refresh_from_db()
        je_id = expense.funding_entry_id

        expense.delete()

        assert not JournalEntry.objects.filter(pk=je_id).exists()
        assert balance(loan) == D("0")

    def test_queryset_delete_deletes_the_entry_too(self, travel, loan):
        expense = make_expense(travel, "69.43", loan)
        expense.refresh_from_db()
        je_id = expense.funding_entry_id

        Expense.objects.filter(pk=expense.pk).delete()

        assert not JournalEntry.objects.filter(pk=je_id).exists()
        assert_books_consistent()

    def test_category_without_a_gl_account_is_refused(self, loan):
        uncategorized = ExpenseCategoryFactory(account=None)
        expense = Expense(category=uncategorized, expense_date=date(2026, 3, 5), amount=D("5.00"),
                          funding_account=loan, billable=False)
        with pytest.raises(ValidationError):
            expense.full_clean()
        with pytest.raises(ValueError, match="no GL account"):
            expense.save()
        assert not Expense.objects.filter(category=uncategorized).exists()

    def test_bank_account_cannot_fund_an_expense(self, card, travel):
        expense = Expense(category=travel, expense_date=date(2026, 3, 5), amount=D("5.00"),
                          funding_account=card.account, billable=False)
        assert "cannot fund an expense" in ExpenseLedgerService.funding_problem(expense)
        assert card.account not in ExpenseLedgerService.funding_accounts()
        assert gl_codes.gl_account(gl_codes.UNAPPLIED_PAYMENTS) not in ExpenseLedgerService.funding_accounts()


class TestFundedExpenseAndBankLinks:
    def test_linking_a_funded_expense_is_refused(self, card, travel, loan):
        expense = make_expense(travel, "40.00", loan)
        txn = deposit(card, "-40.00", "STORE")

        with pytest.raises(ValueError, match="clear its funding account first"):
            BankTransactionService.link_expense(txn, expense)

        txn.refresh_from_db()
        assert txn.expense is None
        assert expense_entries(expense).count() == 1
        assert_books_consistent()

    def test_funded_expense_is_not_offered_for_linking(self, card, travel, loan):
        funded = make_expense(travel, "40.00", loan)
        unfunded = make_expense(travel, "40.00")
        txn = deposit(card, "-40.00", "STORE")

        candidates = BankTransactionLinkExpenseForm(transaction=txn).fields["expense"].queryset
        assert unfunded in candidates and funded not in candidates

    def test_clear_the_funding_account_then_link(self, card, travel, loan):
        expense = make_expense(travel, "40.00", loan)
        txn = deposit(card, "-40.00", "STORE")

        expense.funding_account = None
        expense.save()
        BankTransactionService.link_expense(txn, expense)

        assert expense_entries(expense).count() == 1  # the card charge's entry only
        assert balance(loan) == D("0")
        assert balance(travel.account) == D("40.00")
        assert_books_consistent()

    def test_linked_expense_cannot_also_be_funded(self, card, travel, loan):
        _, expense = charge_linked_to_expense(card, travel)

        expense.funding_account = loan
        with pytest.raises(ValidationError):
            expense.full_clean()
        with pytest.raises(ValueError, match="linked to a bank transaction"):
            expense.save()

        expense.refresh_from_db()
        assert expense.funding_account is None
        assert balance(loan) == D("0")
        assert_books_consistent()

    def test_split_purchase_is_two_expenses(self, card, travel, loan):
        """An Amazon order: $17.21 on the card, $69.43 from a gift-card balance."""
        card_part = deposit(card, "-17.21", "AMAZON")
        on_card = make_expense(travel, "17.21", description="Amazon order (card)")
        BankTransactionService.link_expense(card_part, on_card)
        make_expense(travel, "69.43", loan, description="Amazon order (gift card)")

        assert balance(travel.account) == D("86.64")
        assert balance(loan) == D("-69.43")
        assert balance(card.account) == D("-17.21")
        assert register_equals_gl(card)
        assert_books_consistent()


class TestFundedExpenseViews:
    def form_data(self, category, funding_account, amount="69.43"):
        return {
            "expense_date": "2026-03-05",
            "client": "",
            "category": category.pk,
            "amount": amount,
            "description": "Amazon gift card",
            "funding_account": funding_account.pk if funding_account else "",
        }

    def test_create_edit_delete(self, logged_in, travel, loan):
        logged_in.post(reverse("billing:expense_create"), self.form_data(travel, loan))
        expense = Expense.objects.get(description="Amazon gift card")
        assert expense.funding_entry is not None
        je_id = expense.funding_entry_id
        detail = logged_in.get(reverse("billing:expense_detail", args=[expense.pk]))
        assert reverse("accounting:journal_detail", args=[je_id]).encode() in detail.content

        logged_in.post(reverse("billing:expense_edit", args=[expense.pk]), self.form_data(travel, loan, "70.00"))
        expense.refresh_from_db()
        assert expense.funding_entry_id == je_id
        assert balance(loan) == D("-70.00")

        logged_in.post(reverse("billing:expense_delete", args=[expense.pk]))
        assert not Expense.objects.filter(pk=expense.pk).exists()
        assert not JournalEntry.objects.filter(pk=je_id).exists()
        assert_books_consistent()

    def test_form_shows_the_funding_problem(self, logged_in, loan):
        uncategorized = ExpenseCategoryFactory(account=None)
        response = logged_in.post(reverse("billing:expense_create"), self.form_data(uncategorized, loan))
        assert response.status_code == 200
        assert "no GL account" in str(response.context["form"].errors["funding_account"])
        assert not Expense.objects.filter(description="Amazon gift card").exists()


# --- D. manual journal entries -------------------------------------------------------

class TestManualJournalEntries:
    def test_balanced_entry_is_posted(self, loan, gl):
        je = JournalEntryService.create_manual_entry(
            date(2026, 10, 31), "Reclassify owner contributions",
            [(owner_equity(), D("500.00"), None), (loan, None, D("500.00")), (gl["1000"], None, None)],
        )
        assert je.is_manual
        assert entry_date(je) == date(2026, 10, 31)
        assert lines(je) == sorted([
            (gl_codes.OWNER_EQUITY, D("500.00"), D("0")),
            (gl_codes.SHAREHOLDER_LOAN, D("0"), D("500.00")),
        ])
        assert_books_consistent()

    @pytest.mark.parametrize("bad_lines, message", [
        ([("equity", "500.00", None), ("loan", None, "400.00")], "do not equal"),
        ([("equity", "500.00", None)], "at least two"),
        ([("equity", "500.00", "500.00"), ("loan", None, "0")], "at least two"),
        ([("equity", "500.00", "500.00"), ("loan", "1.00", None), ("loan", None, "1.00")], "not both"),
        ([("equity", "-5.00", None), ("loan", None, "-5.00")], "negative"),
    ])
    def test_invalid_entries_are_refused(self, loan, bad_lines, message):
        accounts = {"equity": owner_equity(), "loan": loan}
        rows = [(accounts[a], D(d) if d else None, D(c) if c else None) for a, d, c in bad_lines]
        with pytest.raises(ValueError, match=message):
            JournalEntryService.create_manual_entry(date(2026, 10, 31), "Bad", rows)
        assert not JournalEntry.objects.filter(is_manual=True).exists()

    def test_bank_and_inactive_accounts_are_refused(self, checking, loan, gl):
        with pytest.raises(ValueError, match="cannot be used"):
            JournalEntryService.create_manual_entry(
                date(2026, 10, 31), "Bad", [(checking.account, D("5"), None), (loan, None, D("5"))]
            )
        legacy_ar = gl["1200"]
        legacy_ar.is_active = False
        legacy_ar.save()
        with pytest.raises(ValueError, match="cannot be used"):
            JournalEntryService.create_manual_entry(
                date(2026, 10, 31), "Bad", [(legacy_ar, D("5"), None), (loan, None, D("5"))]
            )
        assert register_equals_gl(checking)

    def test_reverse_swaps_every_line(self, loan):
        je = JournalEntryService.create_manual_entry(
            date(2026, 10, 31), "Reclass", [(owner_equity(), D("500"), None), (loan, None, D("500"))]
        )
        reversal = JournalEntryService.reverse_entry(je, date(2026, 11, 1))

        assert reversal.is_manual and reversal.reverses == je and je.reversed_by == reversal
        assert entry_date(reversal) == date(2026, 11, 1)
        assert lines(reversal) == sorted([
            (gl_codes.OWNER_EQUITY, D("0"), D("500.00")),
            (gl_codes.SHAREHOLDER_LOAN, D("500.00"), D("0")),
        ])
        assert balance(loan) == D("0") and code_balance(gl_codes.OWNER_EQUITY) == D("0")
        assert_books_consistent()

    def test_reversals_that_are_refused(self, checking, loan):
        je = JournalEntryService.create_manual_entry(
            date(2026, 10, 31), "Reclass", [(owner_equity(), D("500"), None), (loan, None, D("500"))]
        )
        reversal = JournalEntryService.reverse_entry(je, date(2026, 11, 1))

        with pytest.raises(ValueError, match="already been reversed"):
            JournalEntryService.reverse_entry(je, date(2026, 11, 2))
        with pytest.raises(ValueError, match="itself a reversal"):
            JournalEntryService.reverse_entry(reversal, date(2026, 11, 2))
        import_entry = deposit(checking, "10.00").journal_entry
        with pytest.raises(ValueError, match="Only manual entries"):
            JournalEntryService.reverse_entry(import_entry, date(2026, 11, 2))
        assert JournalEntry.objects.filter(is_manual=True).count() == 2
        assert_books_consistent()


class TestManualJournalEntryViews:
    def post_entry(self, client, rows, memo="Reclassify"):
        data = {
            "date": "2026-10-31", "memo": memo,
            "lines-TOTAL_FORMS": "4", "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000",
        }
        for i, (account, debit, credit) in enumerate(rows):
            data[f"lines-{i}-account"] = account.pk
            data[f"lines-{i}-debit"] = debit
            data[f"lines-{i}-credit"] = credit
        return client.post(reverse("accounting:journal_create"), data, follow=True)

    def test_create_list_and_reverse(self, logged_in, loan):
        assert logged_in.get(reverse("accounting:journal_create")).status_code == 200
        response = self.post_entry(logged_in, [(owner_equity(), "500.00", ""), (loan, "", "500.00")])
        je = JournalEntry.objects.get(is_manual=True)
        assert response.redirect_chain[-1][0] == reverse("accounting:journal_detail", args=[je.pk])
        assert response.context["can_reverse"]

        listing = logged_in.get(reverse("accounting:journal_list"))
        assert je in list(listing.context["entries"])
        assert b"Manual" in listing.content

        response = logged_in.post(reverse("accounting:journal_reverse", args=[je.pk]), {"date": "2026-11-01"}, follow=True)
        assert je.reversed_by is not None
        assert f"JE #{je.id} reversed by JE #{je.reversed_by.id}." in message_texts(response)
        assert_books_consistent()

    def test_unbalanced_entry_is_shown_and_not_posted(self, logged_in, loan):
        response = self.post_entry(logged_in, [(owner_equity(), "500.00", ""), (loan, "", "400.00")])
        assert "do not equal" in response.context["entry_error"]
        assert not JournalEntry.objects.filter(is_manual=True).exists()

    def test_viewer_cannot_post_or_reverse(self, viewer, loan):
        response = self.post_entry(viewer, [(owner_equity(), "500.00", ""), (loan, "", "500.00")])
        assert response.status_code == 403
        je = JournalEntryService.create_manual_entry(
            date(2026, 10, 31), "Reclass", [(owner_equity(), D("5"), None), (loan, None, D("5"))]
        )
        assert viewer.post(reverse("accounting:journal_reverse", args=[je.pk]), {"date": "2026-11-01"}).status_code == 403
        assert je.reversed_by is None


# --- E. apply a payment's unapplied balance --------------------------------------------

@pytest.fixture
def underapplied_payment(gl):
    """Production's payment 9: $1,759.68 received, $1,759.00 applied to a
    $1,759.68 invoice already marked PAID, $0.68 left in 2200."""
    client = ClientFactory()
    invoice = issued_invoice(client, "1759.68")
    invoice.status = InvoiceStatus.PAID
    invoice.save()
    payment = Payment.objects.create(
        client=client, date=date(2026, 1, 20), amount=D("1759.68"),
        method=PaymentMethod.ACH, unapplied_amount=D("0.68"),
    )
    PaymentApplication.objects.create(payment=payment, invoice=invoice, amount=D("1759.00"))
    payment.post_to_accounting()
    return payment, invoice


def payment_entries(payment):
    return JournalEntry.objects.filter(
        source_content_type=ContentType.objects.get_for_model(Payment), source_object_id=payment.id,
    )


class TestApplyUnapplied:
    def test_applies_the_remainder_and_clears_2200_and_ar(self, underapplied_payment):
        payment, invoice = underapplied_payment
        assert code_balance(gl_codes.UNAPPLIED_PAYMENTS) == D("-0.68")
        assert code_balance(gl_codes.ACCOUNTS_RECEIVABLE) == D("0.68")
        assert invoice in PaymentService.invoices_open_to(payment)

        PaymentService.apply_unapplied(payment, invoice, D("0.68"), date(2026, 11, 1))

        payment.refresh_from_db()
        invoice.refresh_from_db()
        assert payment.unapplied_amount == D("0")
        assert payment.applications.get().amount == D("1759.68")
        assert invoice.outstanding_balance() == D("0")
        assert invoice.status == InvoiceStatus.PAID
        assert code_balance(gl_codes.UNAPPLIED_PAYMENTS) == D("0")
        assert code_balance(gl_codes.ACCOUNTS_RECEIVABLE) == D("0")
        # The payment keeps exactly one JE of its own; re-posting finds it.
        assert payment_entries(payment).count() == 1
        assert payment.post_to_accounting() == payment_entries(payment).get()
        assert_books_consistent()

    def test_issued_invoice_becomes_paid(self, gl):
        client = ClientFactory()
        first, second = issued_invoice(client, "100.00"), issued_invoice(client, "50.00")
        payment = Payment.objects.create(
            client=client, date=date(2026, 1, 20), amount=D("150.00"),
            method=PaymentMethod.ACH, unapplied_amount=D("50.00"),
        )
        PaymentApplication.objects.create(payment=payment, invoice=first, amount=D("100.00"))
        payment.post_to_accounting()

        PaymentService.apply_unapplied(payment, second, D("50.00"), date(2026, 2, 1))

        second.refresh_from_db()
        assert second.status == InvoiceStatus.PAID
        assert payment.applications.count() == 2
        assert code_balance(gl_codes.ACCOUNTS_RECEIVABLE) == D("0")
        assert_books_consistent()

    @pytest.mark.parametrize("amount, message", [
        ("0.69", "unapplied"),
        ("0", "positive"),
    ])
    def test_amount_limits(self, underapplied_payment, amount, message):
        payment, invoice = underapplied_payment
        with pytest.raises(ValueError, match=message):
            PaymentService.apply_unapplied(payment, invoice, D(amount), date(2026, 11, 1))
        payment.refresh_from_db()
        assert payment.unapplied_amount == D("0.68")

    def test_cannot_exceed_the_invoice_balance(self, gl):
        client = ClientFactory()
        invoice = issued_invoice(client, "10.00")
        payment = Payment.objects.create(client=client, date=date(2026, 1, 20), amount=D("50.00"),
                                         method=PaymentMethod.ACH, unapplied_amount=D("50.00"))
        payment.post_to_accounting()
        with pytest.raises(ValueError, match="outstanding"):
            PaymentService.apply_unapplied(payment, invoice, D("20.00"), date(2026, 2, 1))

    def test_another_clients_invoice_is_refused(self, underapplied_payment):
        payment, _ = underapplied_payment
        stranger = issued_invoice(ClientFactory(), "5.00")
        with pytest.raises(ValueError, match="different client"):
            PaymentService.apply_unapplied(payment, stranger, D("0.68"), date(2026, 11, 1))

    def test_unposted_payment_gets_no_extra_entry(self, gl):
        client = ClientFactory()
        invoice = issued_invoice(client, "40.00")
        payment = Payment.objects.create(client=client, date=date(2026, 1, 20), amount=D("40.00"),
                                         method=PaymentMethod.ACH, unapplied_amount=D("40.00"))
        entries = JournalEntry.objects.count()

        PaymentService.apply_unapplied(payment, invoice, D("40.00"), date(2026, 2, 1))

        assert JournalEntry.objects.count() == entries
        payment.post_to_accounting()
        assert code_balance(gl_codes.ACCOUNTS_RECEIVABLE) == D("0")
        assert code_balance(gl_codes.UNAPPLIED_PAYMENTS) == D("0")
        assert_books_consistent()

    def test_view(self, logged_in, underapplied_payment):
        payment, invoice = underapplied_payment
        url = reverse("accounting:payment_apply_unapplied", args=[payment.pk])
        page = logged_in.get(url)
        assert page.status_code == 200
        assert page.context["form"]["amount"].value() == D("0.68")

        response = logged_in.post(url, {"invoice": invoice.pk, "amount": "0.68", "date": "2026-11-01"}, follow=True)
        assert f"Applied $0.68 to invoice {invoice.invoice_number}." in message_texts(response)
        payment.refresh_from_db()
        assert payment.unapplied_amount == D("0")
        assert_books_consistent()

    def test_view_shows_a_refusal(self, logged_in, underapplied_payment):
        payment, invoice = underapplied_payment
        response = logged_in.post(
            reverse("accounting:payment_apply_unapplied", args=[payment.pk]),
            {"invoice": invoice.pk, "amount": "5.00", "date": "2026-11-01"},
        )
        assert response.status_code == 200
        assert "unapplied" in str(response.context["form"].non_field_errors())

    def test_viewer_cannot_apply(self, viewer, underapplied_payment):
        payment, invoice = underapplied_payment
        response = viewer.post(
            reverse("accounting:payment_apply_unapplied", args=[payment.pk]),
            {"invoice": invoice.pk, "amount": "0.68", "date": "2026-11-01"},
        )
        assert response.status_code == 403


# --- F. unmatch -----------------------------------------------------------------------

class TestUnmatch:
    def test_unmatching_an_expense_restores_the_import_entry(self, card, travel):
        txn, expense = charge_linked_to_expense(card, travel, "-21.80")

        BankTransactionService.unmatch(txn)

        txn.refresh_from_db()
        expense.refresh_from_db()
        assert txn.expense is None and not txn.is_matched
        assert txn.offset_account == owner_equity()
        assert [je.id for je in txn_entries(txn)] == [txn.journal_entry_id]
        assert lines(txn.journal_entry) == sorted([
            (gl_codes.OWNER_EQUITY, D("21.80"), D("0")),
            (card.account.code, D("0"), D("21.80")),
        ])
        assert expense.payment_account is None
        assert not expense_entries(expense).exists()
        assert balance(travel.account) == D("0")
        assert_books_consistent()

    def test_unmatch_to_a_chosen_account_and_delete_the_expense(self, card, travel, loan):
        txn, expense = charge_linked_to_expense(card, travel, "-21.80")

        BankTransactionService.unmatch(txn, offset_account=loan, delete_expense=True)

        txn.refresh_from_db()
        assert txn.offset_account == loan
        assert not Expense.objects.filter(pk=expense.pk).exists()
        assert balance(loan) == D("21.80")
        assert_books_consistent()

    def test_billed_expense_is_not_deleted(self, card, travel):
        txn, expense = charge_linked_to_expense(card, travel)
        Expense.objects.filter(pk=expense.pk).update(status=BillableStatus.BILLED)
        before = lines(txn.journal_entry)

        with pytest.raises(ValueError, match="on an invoice"):
            BankTransactionService.unmatch(txn, delete_expense=True)

        txn.refresh_from_db()
        assert txn.expense_id == expense.pk
        assert lines(txn.journal_entry) == before
        assert_books_consistent()

    def test_unmatching_a_transfer_restores_both_sides(self, checking, card):
        out = deposit(checking, "-500.00", "PAYMENT TO CARD")
        into = deposit(card, "500.00", "PAYMENT THANK YOU")
        BankTransactionService.match_transfer(out, into)

        BankTransactionService.unmatch(into)

        for txn in (out, into):
            txn.refresh_from_db()
            assert txn.transfer_pair is None and not txn.is_matched
            assert txn.offset_account == owner_equity()
            assert [je.id for je in txn_entries(txn)] == [txn.journal_entry_id]
        assert_books_consistent()

    def test_payment_link_is_not_unmatched(self, checking):
        txn = deposit(checking, "100.00")
        payment = Payment.objects.create(client=ClientFactory(), date=date(2026, 1, 13), amount=D("100.00"),
                                         method=PaymentMethod.ACH, unapplied_amount=D("100.00"))
        BankTransactionService.link_existing_payment(txn, payment)
        with pytest.raises(ValueError, match="payment"):
            BankTransactionService.unmatch(txn)
        assert_books_consistent()

    def test_unmatched_transaction_is_refused(self, checking):
        with pytest.raises(ValueError, match="not matched"):
            BankTransactionService.unmatch(deposit(checking, "10.00"))


class TestDeletingAMatchedExpense:
    def test_delete_view_unmatches_and_leaves_no_entry_behind(self, logged_in, card, travel):
        txn, expense = charge_linked_to_expense(card, travel, "-21.80")
        page = logged_in.get(reverse("billing:expense_delete", args=[expense.pk]))
        assert b"Deleting it unmatches that transaction" in page.content

        logged_in.post(reverse("billing:expense_delete", args=[expense.pk]))

        txn.refresh_from_db()
        assert not Expense.objects.filter(pk=expense.pk).exists()
        assert txn.expense is None and txn.offset_account == owner_equity()
        assert not expense_entries(expense).exists()
        assert balance(travel.account) == D("0")
        assert_books_consistent()

    def test_delete_view_refuses_when_the_entry_belongs_to_something_else(self, logged_in, card, travel):
        txn, expense = charge_linked_to_expense(card, travel)
        stray = deposit(card, "-1.00", "OTHER")
        BankTransaction.objects.filter(pk=txn.pk).update(journal_entry=stray.journal_entry)

        response = logged_in.post(reverse("billing:expense_delete", args=[expense.pk]), follow=True)

        assert Expense.objects.filter(pk=expense.pk).exists()
        assert any(m.startswith("Could not delete the expense") for m in message_texts(response))
        assert expense_entries(expense).count() == 1

    def test_queryset_delete_unmatches_too(self, card, travel):
        txn, expense = charge_linked_to_expense(card, travel)

        Expense.objects.filter(pk=expense.pk).delete()

        txn.refresh_from_db()
        assert txn.expense is None and txn.journal_entry is not None
        assert not expense_entries(expense).exists()
        assert_books_consistent()


class TestUnmatchViews:
    def test_register_offers_unmatch_and_the_view_unmatches(self, logged_in, card, travel, loan):
        txn, expense = charge_linked_to_expense(card, travel, "-21.80")
        register = logged_in.get(reverse("accounting:bankaccount_register", args=[card.pk]), {"date_preset": "all"})
        url = reverse("accounting:banktransaction_unmatch", args=[txn.pk])
        assert url.encode() in register.content

        page = logged_in.get(url)
        assert page.status_code == 200 and page.context["expense_deletable"]

        response = logged_in.post(url, {"offset_account": loan.pk, "delete_expense": "on"}, follow=True)
        assert "Transaction unmatched and posted to 2500 Loan from Shareholder." in message_texts(response)
        txn.refresh_from_db()
        assert txn.offset_account == loan
        assert not Expense.objects.filter(pk=expense.pk).exists()
        assert_books_consistent()

    def test_viewer_cannot_unmatch(self, viewer, card, travel):
        txn, _ = charge_linked_to_expense(card, travel)
        response = viewer.post(
            reverse("accounting:banktransaction_unmatch", args=[txn.pk]), {"offset_account": owner_equity().pk}
        )
        assert response.status_code == 403
        txn.refresh_from_db()
        assert txn.is_matched


# --- interplay with the ledger repair ---------------------------------------------------

def test_repair_command_finds_nothing_to_change(gl, checking, card, travel, loan, underapplied_payment):
    """Funded expenses, manual entries, applied balances and unmatches are all
    shapes repair_ledger_postings must leave alone."""
    from io import StringIO

    from django.core.management import call_command

    ChartOfAccount.objects.filter(pk=gl["1200"].pk).update(is_active=False)  # as in production since 3.3.4
    make_expense(travel, "69.43", loan)
    JournalEntryService.create_manual_entry(
        date(2026, 10, 31), "Reclass", [(owner_equity(), D("5"), None), (loan, None, D("5"))]
    )
    payment, invoice = underapplied_payment
    PaymentService.apply_unapplied(payment, invoice, D("0.68"), date(2026, 11, 1))
    txn, _ = charge_linked_to_expense(card, travel)
    BankTransactionService.unmatch(txn, offset_account=loan)
    BankTransactionService.retag_transaction(deposit(checking, "100.00"), loan)

    out = StringIO()
    call_command("repair_ledger_postings", stdout=out)

    assert "0 change(s) found" in out.getvalue(), out.getvalue()
    assert_books_consistent()
