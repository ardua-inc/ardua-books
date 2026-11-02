# Changelog

All notable changes to Ardua Books are recorded here. The canonical version
number lives in `ardua_books/ardua_books/settings.py` (`VERSION`) and is
surfaced by the `/about/` page.

## [3.3.6] - 2026-11-01

- New accounts 2500 Loan from Shareholder, 6110 Rent, 6120 Vehicle Expenses and 6130 Repairs & Maintenance, with matching expense categories (data migration; existing codes are left alone).
- Recategorize an unmatched bank transaction to any account, one at a time or by ticking several in the register (all or none); the register can search descriptions. Bank and card accounts are never offered as an offset, since their GL account moves only with their register.
- Expenses paid outside the business accounts: an expense's new *Paid from* account (e.g. 2500) posts DR expense / CR that account; editing rebuilds the entry and deleting removes it. Linking such an expense to a bank transaction is refused until its funding account is cleared; a split purchase is two expenses. The 7 existing unposted expenses are left as they are.
- Manual journal entries (balanced, two or more lines, no bank or card accounts) with a reverse action; listed in the journal with a Manual badge.
- Apply a payment's unapplied balance to an invoice: DR 2200 / CR AR, and the invoice's balance and status follow.
- Unmatch an expense link or a transfer: the match's entry is deleted and the transaction is posted against a chosen account (Owner Equity by default). Deleting a matched expense now unmatches its transaction instead of leaving its journal entry behind.

## [3.3.5] - 2026-11-01

- "Mark as Owner Equity" in the bank register works. It called a service method that did not exist, so every use failed with "Could not mark as equity". It re-posts an unmatched transaction against Owner Equity 3000 (a deposit as a contribution, a withdrawal as a draw), rebuilding its journal entry in place, or creating one if it has none. A transaction matched to a payment, expense or transfer is refused, since its entry belongs to that match.
- Linking a bank deposit to a payment dates the payment's journal entry on the deposit date, as it already did the payment itself. A payment entered before its deposit kept an entry dated on the earlier day, so dated reports showed the cash and the receivable settling in the wrong period (production: payments 1–6, by 5 to 31 days).
- `repair_ledger_postings` gains step 5: re-date payment entries to their deposit's date. Balances do not change. Production dry run: 6 changes (payments 1–6), nothing else.
- `BankTransactionService.create_import_entry` is the one place an unmatched transaction's journal entry is created (import, recategorize, and the repair's restore step).

## [3.3.4] - 2026-11-01

- Linking a bank deposit to a client payment now leaves exactly one journal entry: the payment's, debiting the deposit's own GL account. Previously the deposit's import entry (DR bank / CR Owner Equity) stayed live and the payment debited 1000 Cash, so every linked receipt was counted twice (production: 12 payments, $19,600.86 in Cash that was really in Wells Fargo, Owner Equity overstated by the same). A payment recorded without a deposit still debits 1000 Cash (undeposited funds) until it is linked. A payment can no longer be linked to two deposits.
- Payments credit Accounts Receivable 1100, the account invoices debit. They credited a second "Accounts Receivable", 1200, since the QuickBooks import (production: −$12,442.49 on 1200 against $17,183.32 on 1100). GL codes now live in one module, `accounting/gl_codes.py`.
- A credit (refund or statement credit) linked to an expense is posted as a contra-expense (DR bank / CR expense) and recorded as a negative expense. It was posted as a charge, overstating expenses by twice the amount (production: 7 transactions, $2,204.36). Linking a credit to a positive expense, or a charge to a negative one, is refused.
- A transfer must pair one withdrawal with one deposit or payment; the transfer form only offers opposite amounts. Two same-sized withdrawals could be paired and were posted backwards (production: Wells Fargo txn 293 with Marriott's Costco charge 489).
- New `repair_ledger_postings` command repairs data posted by these bugs. Dry run by default (read-only, and runnable under `books_readonly`); `--apply` makes every change in one transaction and rolls back unless the result matches the dry run and every bank register equals its GL account.

## [3.3.3] - 2026-11-01

- Bank CSV import counts duplicates instead of collapsing them. Genuinely repeated transactions (identical date, description and amount, e.g. two same-day Uber charges) are all imported. Re-importing a file, or a grown year-to-date file, still adds only the rows not already present. Previously such repeats were silently dropped, and production's Amex account was short 6 charges ($5.77).
- Duplicate matching ignores differences in spacing within a description. Amex re-padded some descriptions between 2026 exports (`AMAZON MARKEPLACE NA PA` → `AMAZON MARKEPLACE NA  PA`); compared exactly, a re-import would have booked those 3 charges ($607.30) a second time. Stored descriptions keep the bank's spacing.
- The import validates every row before writing anything. A bad row (too few columns, unparseable date or amount, more than 2 decimal places, over-long description) rejects the file with its line number instead of a 500 or a partial import.
- Blank rows, headers and empty date cells are skipped without crashing. A UTF-8 byte-order mark no longer hides the first row of a header-less file.
- Amount cells tolerate `$`, thousands separators and spaces (Citi's `-$ 1,308.35`).
- Imports into the same account are serialised (row lock), so two concurrent imports of one file cannot both insert it.
- New read-only `audit_import_duplicates` command: compares a CSV with an account and predicts what a re-import would add.

## [3.3.2] - 2026-11-01

- Added `books_readonly`, a read-only Postgres login for production reporting. Credential-bearing tables and `auth_user.password` are denied, and statement and idle-transaction timeouts protect deploys.
- The role is defined by the idempotent `ardua_books/deploy/readonly_role.sql`, which every deploy re-applies after `migrate`.
- Published Postgres on books' loopback (`127.0.0.1:5432`) for SSH-tunnelled access; it is not reachable from the LAN.
- Added `ardua_books/deploy/set_readonly_password.py` to set or rotate the password from homer. It sends only a SCRAM verifier to the server and writes `~/.pgpass` and `~/.pg_service.conf`.
- Restore Runbook: `pg_restore --no-acl`, then re-apply the role.

## [3.3.1] - 2026-08-11

- Consolidated dependencies into a single `ardua_books/requirements.txt`; removed the drifted root-level copy that pinned a different Django.
- Corrected the Django pin from `==5.0,<6.0` (an exact pin to end-of-life 5.0) to `>=5.2,<5.3`.
- Pinned `django-allauth[socialaccount]` so the SSO dependencies survive the pending upgrade to allauth 65.x.
- `DJANGO_DEBUG` now defaults to False instead of True, so a deployment with no value set fails closed.
- Closed self-service registration at `/accounts/signup/` with `NoSignupAccountAdapter`; removed the no-op `ACCOUNT_ALLOW_REGISTRATION` setting that was believed to be doing this.
- Set `ACCOUNT_AUTHENTICATION_METHOD` alongside `ACCOUNT_LOGIN_METHODS` so email login works on both the pinned and the upcoming allauth.
- Added `tests/test_auth_access.py` covering the authentication surface.
- Fixed two long-standing invoice test failures: they asserted on `Invoice.sequence`, which stopped being assigned when invoice numbering was reworked. Rewritten to assert on `invoice_number`; the numbering logic itself is unchanged.

## [3.3.0] - baseline

- Baseline entry. Changes prior to 3.3.1 were not tracked in this file; see
  the git history for detail.
