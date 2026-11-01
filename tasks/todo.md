# Read-only production DB login (books_readonly)

Plan: ~/.claude/plans/zippy-roaming-rose.md (approved 2026-11-01)

- [x] `ardua_books/deploy/readonly_role.sql` — idempotent role + grants + denylist, in one transaction
- [x] `docker-compose.yml` — publish db on 127.0.0.1:5432 only
- [x] `deploy.yml` — sync the SQL file, re-apply it after every migrate
- [x] `ardua_books/deploy/set_readonly_password.py` — local SCRAM verifier, ~/.pgpass, ~/.pg_service.conf
- [x] `ardua_books/tests/test_readonly_password.py` — RFC 7677 vector, file writers
- [x] Docs: README, Restore Runbook, CHANGELOG, VERSION 3.3.2
- [x] Full test suite green (82 passed)
- [ ] Commit, push, tag v3.3.2, watch deploy
- [ ] homer: `brew install libpq`, run set_readonly_password.py
- [ ] Verify A–G (A = trust-rule bypass check; roll back immediately if it fails)

## Review
