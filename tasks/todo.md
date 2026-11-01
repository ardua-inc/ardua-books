# Read-only production DB login (books_readonly)

Plan: ~/.claude/plans/zippy-roaming-rose.md (approved 2026-11-01)

- [x] `ardua_books/deploy/readonly_role.sql` — idempotent role + grants + denylist, in one transaction
- [x] `docker-compose.yml` — publish db on 127.0.0.1:5432 only
- [x] `deploy.yml` — sync the SQL file, re-apply it after every migrate
- [x] `ardua_books/deploy/set_readonly_password.py` — local SCRAM verifier, ~/.pgpass, ~/.pg_service.conf
- [x] `ardua_books/tests/test_readonly_password.py` — RFC 7677 vector, file writers
- [x] Docs: README, Restore Runbook, CHANGELOG, VERSION 3.3.2
- [x] Full test suite green (82 passed)
- [x] Commit, push, tag v3.3.2, watch deploy (7973587, run 41326847235 green)
- [x] homer: `brew install libpq`, run set_readonly_password.py
- [x] Verify A–G — all passed

## Review
Shipped as v3.3.2 and verified on production, 2026-11-01.

- **A, trust bypass:** through the tunnel, superuser `ardua` is refused both without a password and with a wrong one. Clients arrive from the Docker gateway `172.18.0.1`, never `127.0.0.1`, so the image's loopback `trust` rule is unreachable.
- **B:** port 5432 is not reachable from the LAN; it's bound to `127.0.0.1` on books.
- **C:** business data is readable (14 invoices, 3 clients, 921 journal entries).
- **D:** these are all denied:
  - `auth_user.password`
  - `django_session`
  - `socialaccount_socialtoken` and `socialaccount_socialapp`
  - `account_emailconfirmation`
  - `CREATE`, `INSERT`, `UPDATE`, `DELETE` — still refused by privileges after `SET default_transaction_read_only = off`
- **E:** `auth_user` username and email are readable.
- **F:** `statement_timeout` is 2min and `idle_in_transaction_session_timeout` is 5min.
- **G:** re-applying the script on prod is a clean no-op, and the password survives it.
- **Pre-deploy:** the SQL was also validated against a schema-only copy of prod in a throwaway container on books: idempotent, and simulated `mfa_*`/`usersessions_*` tables were denied after re-apply.
- **Password tool:** checked against the RFC 7677 example exchange and mutation-tested (3 of 3 mutations caught).
