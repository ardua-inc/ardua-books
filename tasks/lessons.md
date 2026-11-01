# Lessons

## Never `export` production database settings in a shell that runs tests (2026-11-01)

**What happened:** pytest ran in the same shell where `POSTGRES_HOST/PORT/USER/PASSWORD` had been exported for the production SSH tunnel (`books_readonly`). Django settings read those env vars, so pytest-django tried to create `test_ardua_books` on the production server. It failed only because `books_readonly` has no `CREATEDB`; with the `ardua` superuser it would have created, and later dropped, a database on production.

**Rule:** pass production settings per command (`POSTGRES_HOST=… ../venv/bin/python manage.py …`) or in a subshell `( export …; … )`, never with a bare `export` in a shell that later runs tests. Before running pytest, `env | grep ^POSTGRES_` must be empty.
