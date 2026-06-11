# Backlog

## Code review follow-ups (2026-06-10/11)

- [ ] `accounting/services/banking.py`: `suggest_category` iterates all active `TransactionRule`s in Python for every transaction in the batch view. Fine at current scale, but revisit if rule/transaction counts grow. Also remove unused `ExpenseCategory` import.
- [ ] `accounting/models.py`: `TransactionRule.description_contains` has no validation against an empty string, which would match every transaction description. Add a `MinLengthValidator` or form-level check.
- [ ] `accounting/forms.py` / `TransactionRuleForm`: no validation for conflicting/duplicate rules (same `description_contains` + overlapping `bank_account` scope). Document or test that ties are broken by `name` ordering.
- [ ] `accounting/views/bank_views.py`: `TransactionRuleListView` is missing `ReadOnlyUserMixin` for consistency with the other CRUD views (currently harmless since it's GET-only).
- [ ] Minor: add trailing newline to `accounting/services/banking.py` and `accounting/forms.py`.

## Optional hardening

- [ ] Consider serving HTTPS directly on port 8000 (self-signed cert) so direct LAN access to `books.ardua.lan:8000` is encrypted, in addition to the sideshowbob proxy. Low priority — LAN access is currently an accepted risk.
