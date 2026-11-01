# Changelog

All notable changes to Ardua Books are recorded here. The canonical version
number lives in `ardua_books/ardua_books/settings.py` (`VERSION`) and is
surfaced by the `/about/` page.

## [3.3.3] - 2026-11-01

- Bank CSV import counts duplicates instead of collapsing them. Genuinely repeated transactions (identical date, description and amount, e.g. two same-day Uber charges) are all imported. Re-importing a file, or a grown year-to-date file, still adds only the rows not already present. Previously such repeats were silently dropped, and production's Amex account was short 6 charges ($5.77).
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
