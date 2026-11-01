-- books_readonly: read-only login for ad-hoc reporting against production.
--
-- Idempotent. Applied by .github/workflows/deploy.yml after every `migrate`
-- (so tables added by a migration are picked up, and filtered, on the same
-- deploy), and safe to re-run by hand:
--
--   docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < readonly_role.sql
--
-- Contains no secret. The password is set separately, from homer, by
-- deploy/set_readonly_password.py, which sends only a SCRAM verifier.
--
-- Everything runs in one transaction: the blanket GRANT below is immediately
-- narrowed by the REVOKEs, and a failure part-way must not leave the
-- credential tables readable.

\set ON_ERROR_STOP on

BEGIN;

-- Create the role only if it is missing. Created without a password it cannot
-- authenticate over the network (pg_hba requires scram-sha-256 for every
-- non-loopback address), so a freshly created role is inert until a password
-- is set.
SELECT 'CREATE ROLE books_readonly LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'books_readonly')
\gexec

-- Re-asserted on every run so any drift is corrected.
ALTER ROLE books_readonly
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT
    CONNECTION LIMIT 5;

-- default_transaction_read_only is a convenience, NOT the security boundary:
-- any session can SET it back off. The boundary is that the role holds
-- nothing but SELECT.
ALTER ROLE books_readonly SET default_transaction_read_only = on;

-- Protect production from the reporting user. A long query, or a transaction
-- left open in a GUI client, holds ACCESS SHARE locks that block the
-- ACCESS EXCLUSIVE lock a deploy's `migrate` needs -- and every app query then
-- queues behind the blocked migration.
ALTER ROLE books_readonly SET statement_timeout = '2min';
ALTER ROLE books_readonly SET idle_in_transaction_session_timeout = '5min';

GRANT CONNECT ON DATABASE :"DBNAME" TO books_readonly;
GRANT USAGE ON SCHEMA public TO books_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO books_readonly;

-- Tables holding credentials or anything that can be replayed as one.
-- SELECT on these would let a "read-only" login act as another user:
--   django_session             live session keys -> hijack a logged-in admin
--   socialaccount_socialtoken  Google OAuth access/refresh tokens
--   socialaccount_socialapp    OAuth client secrets
--   account_emailconfirmation  confirmation keys -> account takeover
-- The patterns cover tables the pending allauth 65.x upgrade adds:
--   mfa_%            TOTP secrets and recovery codes
--   usersessions_%   per-user session tracking
--   %token%          any future token store
DO $$
DECLARE
    t text;
BEGIN
    FOR t IN
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND (
                c.relname IN (
                    'django_session',
                    'socialaccount_socialtoken',
                    'socialaccount_socialapp',
                    'account_emailconfirmation'
                )
                OR c.relname LIKE 'mfa\_%'
                OR c.relname LIKE 'usersessions\_%'
                OR c.relname LIKE '%token%'
          )
    LOOP
        EXECUTE format('REVOKE ALL ON public.%I FROM books_readonly', t);
    END LOOP;
END
$$;

-- auth_user: readable, except the password hash. Revoking the table-level
-- privilege also drops any column-level grants, so the column list is rebuilt
-- from the current schema on every run.
REVOKE ALL ON public.auth_user FROM books_readonly;
DO $$
DECLARE
    cols text;
BEGIN
    SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum)
    INTO cols
    FROM pg_attribute
    WHERE attrelid = 'public.auth_user'::regclass
      AND attnum > 0
      AND NOT attisdropped
      AND attname <> 'password';

    EXECUTE format('GRANT SELECT (%s) ON public.auth_user TO books_readonly', cols);
END
$$;

COMMIT;
