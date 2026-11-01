# Backlog

## Code review follow-ups (2026-06-10/11)

- [ ] `accounting/services/banking.py`: `suggest_category` iterates all active `TransactionRule`s in Python for every transaction in the batch view. Fine at current scale, but revisit if rule/transaction counts grow. Also remove unused `ExpenseCategory` import.
- [ ] `accounting/models.py`: `TransactionRule.description_contains` has no validation against an empty string, which would match every transaction description. Add a `MinLengthValidator` or form-level check.
- [ ] `accounting/forms.py` / `TransactionRuleForm`: no validation for conflicting/duplicate rules (same `description_contains` + overlapping `bank_account` scope). Document or test that ties are broken by `name` ordering.
- [ ] `accounting/views/bank_views.py`: `TransactionRuleListView` is missing `ReadOnlyUserMixin` for consistency with the other CRUD views (currently harmless since it's GET-only).
- [ ] Minor: add trailing newline to `accounting/services/banking.py` and `accounting/forms.py`.

## Follow-ups from the dependency consolidation (2026-08-11)

- [ ] **Upgrade django-allauth 0.61.1 → 65.x.** The local venv has been running 65.15.0 while both requirements files pinned 0.61.1, so settings.py was written against APIs production doesn't have. Spans real breaking changes (settings renames, `ACCOUNT_SIGNUP_FIELDS` replacing `ACCOUNT_EMAIL_REQUIRED`, the `socialaccount` extra). Once done: drop the compatibility `ACCOUNT_AUTHENTICATION_METHOD` line in `settings.py` and re-check `ACCOUNT_EMAIL_REQUIRED`.
- [x] ~~Pre-existing test failures in `billing/tests/test_models.py`.~~ Fixed 2026-08-11. Root cause: commit `108053e` deliberately replaced sequence-based numbering with `_generate_next_invoice_number()`, which never assigns `self.sequence`; the tests passed at `3a738bf` and were never updated. Rewritten to assert on `invoice_number`. Production numbering logic was not changed.
- [ ] **Remove the vestigial `Invoice.sequence` field.** Never written and never read anywhere in the codebase since `108053e` — it is permanently 0. Dropping it needs a migration, and `data_export/09_billing_Invoice.json` carries `sequence` values, so the exports must be regenerated or `loaddata` will fail during a restore (see `Restore Runbook.md`).
- [ ] **Invoice numbering is fragile** (`Invoice._generate_next_invoice_number`, introduced in `108053e`). Verified behaviour: `00668 -> 669` (zero-padding silently dropped), `2025-001 -> 2` and `2025-042 -> 43` (the year-prefixed format the commit message claims to support is not preserved, and collides with plain low numbers). It also loads every invoice into Python on each save (O(n) per create) and has a check-then-insert race that will raise a unique-constraint error on `invoice_number` under concurrent creation. Consider a DB sequence or `select_for_update`.
- [ ] **CI runs no tests.** `.github/workflows/deploy.yml` builds and deploys straight to production on every push to `main` with no test step. The two failures above would not have been caught. Add a `pytest` job gating the build.
- [ ] **Cookie/transport hardening flagged by `manage.py check --deploy`:** `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_SSL_REDIRECT` and `SECURE_HSTS_SECONDS` are all unset. Not a one-line fix — the backlog item below accepts plain-HTTP LAN access to `books.ardua.lan:8000`, and flipping the cookie flags would break logins over that path. Resolve alongside the HTTPS-on-8000 item.
- [ ] Consider splitting dev tooling (`black`, `pytest*`, `factory-boy`) into a `requirements-dev.txt` that starts with `-r requirements.txt`. Keeps one authority while dropping test tooling from the production image. Deferred to keep this change tight.

## Ledger repair, after 3.3.4 is merged (2026-11-01)

- [ ] **Back up production, then `repair_ledger_postings --apply`** (Jim). Dry run against production, read-only, 2026-11-01: 48 changes; Cash 19,600.86 → 0; AR on 1100 only, 4,740.83; Owner Equity −59,376.83 → −39,772.69; expenses −2,204.36; all four registers equal their GL accounts. Rehearsed `--apply` on a restored copy: matched the prediction, second run 0 changes, pages render.
- [ ] **Then match the transfer by hand:** Wells Fargo txn 293 ↔ Marriott txn 488 ("Payment Thank You-Mobile", +1.64), and link Marriott txn 489 (Costco, −1.64) to an expense.
- [ ] **`Mark as Owner Equity` is broken:** `BankTransactionMarkOwnerEquityView` calls `BankTransactionService.mark_as_owner_equity`, which does not exist; every use shows "Could not mark as equity: …".
- [ ] **Payment JE dates:** linking a payment updates `payment.date` to the deposit date but not its JE's `posted_at`. Payments 1–6 (QuickBooks import) have JEs dated weeks before their deposits, which shifts AR/cash between periods in dated reports.

## Bank import data repair, after 3.3.3 is merged (2026-11-01)

Latest exports: `~/Documents/Ardua/Statements/CreditCards/<issuer>/<last4>/2026/2026-01-01_to_2026-10-31_transactions.csv`. Production audit of each, read-only via `books_readonly` (2026-11-01):

- [ ] **Amex 3008 (account 2): import the 10-31 YTD file.** It adds 109 and skips 341: the 6 Uber "TRIP HELP" charges the old importer collapsed ($5.77: 2/8 ×2, 2/9 ×2, 2/11, 2/21) plus 103 never imported (nothing after 2026-08-03). This one file supersedes re-importing `activity-7.csv`. It confirms the spec's "5 repeat groups / 7 rows". Afterwards `audit_import_duplicates 2 <file>` should report "Short in Ardua Books: none".
- [ ] **Chase Prime 3876 (account 4): import the 10-31 YTD file.** It adds 69 and skips 48; all 69 were never imported (last import around 2026-04-15), none are collapsed repeats.
- [ ] **Chase Marriott 6330 (account 1): import the 10-31 YTD file.** It adds 1 (2026-10-09 DAN SERVICES INC, $254.00) and skips 10.
- [ ] **Wells Fargo (account 3) has not been audited.** No export of it was on homer.
- Clean, per the same audit: Amex 2025 (`activity-2.csv`), Chase 3876 2025, Chase 6330 2026.

## Optional hardening

- [ ] Consider serving HTTPS directly on port 8000 (self-signed cert) so direct LAN access to `books.ardua.lan:8000` is encrypted, in addition to the sideshowbob proxy. Low priority — LAN access is currently an accepted risk.
