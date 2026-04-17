"""
Tests for TransactionRule matching logic.
"""
import pytest
from decimal import Decimal

from accounting.services.banking import BankTransactionService
from conftest import (
    BankAccountFactory,
    BankTransactionFactory,
    ChartOfAccountFactory,
    ExpenseCategoryFactory,
    TransactionRuleFactory,
)


def make_category(db):
    return ExpenseCategoryFactory(account=ChartOfAccountFactory(type="EXPENSE"))


class TestSuggestCategory:
    def test_matches_description_substring(self, db):
        category = make_category(db)
        TransactionRuleFactory(description_contains="AWS", category=category)
        txn = BankTransactionFactory(description="AWS/us-east-1 charge")
        assert BankTransactionService.suggest_category(txn) == category

    def test_match_is_case_insensitive(self, db):
        category = make_category(db)
        TransactionRuleFactory(description_contains="aws", category=category)
        txn = BankTransactionFactory(description="AWS/us-east-1")
        assert BankTransactionService.suggest_category(txn) == category

    def test_no_match_returns_none(self, db):
        category = make_category(db)
        TransactionRuleFactory(description_contains="AWS", category=category)
        txn = BankTransactionFactory(description="Starbucks Coffee")
        assert BankTransactionService.suggest_category(txn) is None

    def test_inactive_rule_is_skipped(self, db):
        category = make_category(db)
        TransactionRuleFactory(description_contains="AWS", category=category, is_active=False)
        txn = BankTransactionFactory(description="AWS charge")
        assert BankTransactionService.suggest_category(txn) is None

    def test_higher_priority_wins(self, db):
        low_cat = make_category(db)
        high_cat = make_category(db)
        TransactionRuleFactory(description_contains="GUSTO", category=low_cat, priority=0)
        TransactionRuleFactory(description_contains="GUSTO", category=high_cat, priority=10)
        txn = BankTransactionFactory(description="GUSTO PAYROLL")
        assert BankTransactionService.suggest_category(txn) == high_cat

    def test_bank_account_scoped_rule_matches_correct_account(self, db):
        category = make_category(db)
        account = BankAccountFactory()
        TransactionRuleFactory(
            description_contains="AMZN",
            category=category,
            bank_account=account,
        )
        txn = BankTransactionFactory(description="AMZN charge", bank_account=account)
        assert BankTransactionService.suggest_category(txn) == category

    def test_bank_account_scoped_rule_skips_other_account(self, db):
        category = make_category(db)
        account_a = BankAccountFactory()
        account_b = BankAccountFactory()
        TransactionRuleFactory(
            description_contains="AMZN",
            category=category,
            bank_account=account_a,
        )
        txn = BankTransactionFactory(description="AMZN charge", bank_account=account_b)
        assert BankTransactionService.suggest_category(txn) is None

    def test_unscoped_rule_matches_any_account(self, db):
        category = make_category(db)
        TransactionRuleFactory(
            description_contains="GITHUB",
            category=category,
            bank_account=None,
        )
        txn = BankTransactionFactory(
            description="GITHUB subscription",
            bank_account=BankAccountFactory(),
        )
        assert BankTransactionService.suggest_category(txn) == category
