# Mark as Owner Equity + payment JE dates (3.3.5)

Branch: fix/owner-equity-and-payment-dates (PR; main deploys to production)

- [x] `mark_as_owner_equity`: retag to 3000; refuse matched transactions and JEs that belong to another record (guards in `retag_transaction`); create the JE if missing
- [x] `create_import_entry`: one place for an unmatched transaction's JE (post_transaction, retag, repair restore)
- [x] `link_existing_payment` dates the payment JE on the deposit date
- [x] `repair_ledger_postings` step 5: re-date payment JEs (shares step 1's skip rules)
- [x] Tests: service + view per bug, repair step; fail on main, pass on branch; full suite
- [x] Dry run against production (books_readonly)
- [x] Docs, CHANGELOG, VERSION 3.3.5

## Review
- Tests: 161 passed (10 new; 3 repair tests extended). Against main's code 11 failed before the last test was added (the 8 new bug tests and the 3 extended repair tests); the 10th, a retag guard against a JE that belongs to another transaction, calls mark_as_owner_equity and so cannot pass on main either. One new test passes on main as a guard: an unposted payment was already posted on the deposit date.
- Production dry run (read-only): 6 changes, all step 5 (JEs 28, 30, 32, 34, 36, 38 -> deposit dates); steps 1-4 find nothing; balances and registers unchanged.

# Ledger-posting bugs + repair_ledger_postings (3.3.4)

Spec: ~/.hermes/profiles/claude/cache/scratch/ardua-books-ledger-fixes-prompt.md
Plan: ~/.claude/plans/zippy-roaming-rose.md (approved 2026-11-01)
Branch: fix/ledger-posting-bugs (branch only; main deploys to production; never --apply on production)

Decisions:
- Bug 1: payments without a bank transaction keep debiting 1000 Cash (undeposited funds); linking moves the debit to the deposit's GL account and deletes the import JE.
- Bug 2: 1100 is canonical AR; codes centralised in accounting/gl_codes.py; repair deactivates 1200 once empty.
- Bug 3: credits post as contra-expense (DR bank / CR expense); Expense.amount == -txn.amount, so refunds are negative Expenses.
- Bug 4: transfers need one negative and one positive amount; repair restores both import JEs on 3000.

- [x] gl_codes module; Payment.post_to_accounting(deposit_account=); AR -> 1100
- [x] banking service: link_existing_payment, create_payment_from_transaction, link_expense, match_transfer
- [x] forms/views: expense + transfer candidate querysets; new expense amount = -txn.amount
- [x] repair_ledger_postings (plan/apply, dry-run default, READ ONLY on postgres, post-apply cross-check)
- [x] Tests: service + e2e per bug, repair command; fail on main, pass on branch; full suite
- [x] Dry run against production (books_readonly)
- [x] --apply rehearsal on a restored copy (local brew Postgres)
- [x] Docs, CHANGELOG, VERSION 3.3.4; commit; push branch only

## Review
- **Tests:** 151 passed (25 new in `accounting/tests/test_ledger_postings.py`; 2 payment tests updated from AR 1200 to 1100).
  - Against `main`'s code: 22 of the 25 fail for the right reasons. Cash and AR keep the money, the refund posts as a charge, same-sign transfers are accepted, and refunds become positive expenses; the repair tests fail because the command doesn't exist. The 3 that pass are regression guards: an unlinked payment still debits Cash, a charge still posts as an expense, and a real card payment still matches.
- **Dry run against production** (read-only, `books_readonly`): 48 changes.
  - Cash goes from 19,600.86 to 0.
  - AR moves onto 1100 only, at 4,740.83.
  - Owner Equity moves by +19,604.14: the spec's 19,600.86, plus 3.28 from the two restored withdrawals.
  - Expenses drop by 2,204.36.
  - All four registers equal their GL accounts.
- **`--apply` rehearsal** on a `pg_dump` copy restored into a local Postgres 15:
  - The copy's dry run was identical to production's.
  - `--apply` matched the prediction, and a second run found 0 changes.
  - An independent check found no orphan JEs and every JE balanced (921 → 910 JEs).
  - The register, trial balance, AR aging, client balances, reconciliation and expense pages all returned 200.
  - The copy was dropped and the dump deleted afterwards.
- **Out of scope; flagged in the backlog:** `mark_as_owner_equity` doesn't exist, and payment JE `posted_at` isn't updated when a payment is linked.
