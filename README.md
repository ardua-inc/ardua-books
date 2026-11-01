# Ardua Books

Ardua Books is a lightweight billing, invoicing, and accounting system designed for an independent consultant or small consulting firm.

---
## License
MIT License © 2026 Ardua, Inc.

## Features

### Billing & Invoicing
- **Client Management** – Contact info, default rates, payment terms, active/inactive status
- **Time Entry Tracking** – Billable hours with consultant attribution and rate snapshots
- **Expense Tracking** – Categorized expenses with receipt uploads (images/PDFs)
- **Recurring Charges** – Define monthly, quarterly, or annual charges per client (e.g., hosting fees, retainers) that automatically appear as due when creating invoices
- **Invoice Generation** – Auto-numbered invoices (YYYY-NNN format) with status workflow; attach time entries, expenses, and recurring charges
- **PDF Generation** – Professional invoice PDFs with attached receipt pages

### Accounting
- **Chart of Accounts** – Asset, Liability, Equity, Income, Expense account types
- **Journal Entries** – Automatic GL posting when invoices are issued/voided
- **Payment Tracking** – Record payments, allocate to invoices, track unapplied amounts
- **Bank Accounts** – Track checking, savings, credit card, and cash accounts
- **Bank Transaction Import** – CSV import with configurable column mapping and sign rules. Safe to re-import a growing year-to-date file: rows already present are skipped, while genuinely repeated transactions (two identical charges on the same day) are all kept

### Reporting
- **Trial Balance** – Aggregated debits/credits with date filtering
- **Income Statement** – Revenue, expenses, and net income with date filtering
- **AR Aging** – Overdue invoice analysis by aging buckets with client filtering
- **Client Balance Summary** – Outstanding balances by client with sortable columns
- **Journal Entries** – Searchable journal with date filtering and pagination

All list views support:
- Date range presets (MTD, YTD, Last 30 days, Last year, Custom)
- Pagination with configurable page sizes
- Auto-submit filters for quick navigation

### Mobile/PWA
- **Mobile Entry Shell** – Quick time and expense entry at `/m/`
- **Progressive Web App** – Installable on iOS/Android via "Add to Home Screen"
- **Offline Caching** – Service worker caches shell and static assets

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Framework | Django 5.2 LTS |
| Database | SQLite (dev), PostgreSQL (production) |
| PDF Generation | WeasyPrint + pypdf |
| Authentication | Django sessions + CSRF, Google SSO via django-allauth |
| Static Files | WhiteNoise |
| Deployment | Docker + Gunicorn + NGINX |

---

## Repository Layout

```
ardua_books/
├── ardua_books/              # Django project configuration
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py / asgi.py
│
├── billing/                  # Billing & invoicing module
│   ├── models.py             # Client, Consultant, TimeEntry, Expense, Invoice, RecurringCharge
│   ├── services.py           # Invoice generation, item attachment, recurring charge billing
│   ├── views/                # CRUD views + PDF generation
│   ├── forms.py
│   ├── urls.py
│   ├── management/commands/  # Import and utility commands
│   └── templates/billing/
│
├── accounting/               # GL & reporting module
│   ├── models.py             # ChartOfAccount, JournalEntry, Payment, BankAccount
│   ├── services/
│   │   ├── posting.py        # GL posting/reversing
│   │   ├── banking.py        # Bank transaction logic
│   │   ├── importing.py      # CSV import utilities
│   │   └── payment_allocation.py
│   ├── views/                # Payment, bank account, transaction views
│   └── templates/accounting/
│
├── templates/
│   ├── base.html
│   └── registration/
│
├── static/
│   ├── css/
│   └── pwa/                  # Manifest, service worker, mobile JS
│
├── media/                    # Runtime uploads (receipts)
├── deploy/
│   └── nginx.conf
│
├── docker_compose.yml
├── Dockerfile
├── requirements.txt          # Single source of truth for dependencies
└── manage.py
```

> **Dependencies:** `ardua_books/requirements.txt` is the only requirements
> file in this repo, and it is what the Docker image installs. A second copy
> previously sat at the repo root and drifted — pinning a different Django
> than the one that actually shipped. Don't reintroduce one.

---

## Local Development Setup

### 1. Create virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install --upgrade pip
pip install -r ardua_books/requirements.txt
```

### 3. Configure environment

Create `ardua_books/.env`. `DJANGO_DEBUG` defaults to **False**, so local
development must opt in explicitly:

```bash
DJANGO_DEBUG=True
DJANGO_SECRET_KEY=any-long-random-string-for-local-use
```

Leaving `DJANGO_DEBUG` unset anywhere is safe by design — it fails closed
rather than exposing Django's debug pages.

### 4. Apply migrations and create superuser

```bash
cd ardua_books
python manage.py migrate
python manage.py createsuperuser
```

### 5. Run development server

```bash
python manage.py runserver 8000
```

> **Accounts are administrator-provisioned.** Self-service registration is
> closed (`NoSignupAccountAdapter`), and Google SSO only admits addresses that
> already match an existing user. Create users with `createsuperuser` or via
> the Django admin.

**URLs:**
- Main app: http://localhost:8000/
- Admin: http://localhost:8000/admin/
- Mobile: http://localhost:8000/m/

---

## Management Commands

Ardua Books includes several management commands for data import and maintenance.

### Running Commands

**Development (local):**
```bash
cd ardua_books
source ../venv/bin/activate
python manage.py <command> [options]
```

**Production (Docker):**
```bash
docker compose exec web python manage.py <command> [options]
```

### Available Commands

#### Import QuickBooks Invoices

Import historical invoices from a QuickBooks "Sales by Customer Detail" CSV export.

```bash
python manage.py import_qb_invoices path/to/file.csv \
    --income-account 4000 \
    --consultant 1 \
    --expense-category "Equipment" \
    --dry-run
```

**Options:**
| Option | Required | Description |
|--------|----------|-------------|
| `csv_file` | Yes | Path to QuickBooks CSV export |
| `--income-account` | Yes | GL account code for revenue (e.g., 4000) |
| `--consultant` | Yes | Consultant ID for time entries |
| `--expense-category` | No | Expense category name (default: "Equipment") |
| `--dry-run` | No | Preview without making changes |

**What it creates:**
- TimeEntry records (for JMR-* line items)
- Expense records (for EXP line items)
- Invoice with InvoiceLine records linked to time/expenses
- Payment and PaymentApplication (marks invoice as paid)
- Journal entries for both invoice and payment

**Notes:**
- Run one client at a time to specify different income accounts per client
- Work dates are extracted from the memo field (format: "mm/dd/yy - description")
- Duplicate invoice numbers are skipped automatically

#### Clear Transactional Data

Reset the database to a clean state while preserving configuration.

```bash
python manage.py clear_transactions
python manage.py clear_transactions --yes  # Skip confirmation
```

**Preserves:**
- Bank accounts & import profiles
- Chart of accounts (GL accounts)
- Users & groups
- Consultants
- Expense categories
- Clients
- Recurring charge definitions

**Deletes:**
- Journal entries & lines
- Time entries
- Expenses
- Invoices & invoice lines
- Recurring charge occurrences
- Payments & payment applications
- Bank transactions

#### Audit Bank Imports for Missing Duplicates

Read-only. Compares a bank or card CSV with the transactions Ardua Books
already holds for that account, and reports what re-importing the file would
do.

```bash
python manage.py audit_import_duplicates <bank_account_id> path/to/activity.csv
python manage.py audit_import_duplicates 2 activity.csv --all   # list every key
```

**How the import treats duplicates.** The importer counts matching rows rather
than asking whether a match exists. A match means the same date, description
and amount. For each one it imports `count in file − count already in the
account`, never less than zero. So:

- re-importing a file adds nothing;
- a year-to-date file that has grown adds only the new rows;
- two identical charges on the same day are both kept.

Before 3.3.3 the importer kept only one of each such pair, and still reported
success, which left the account understated.

**What the audit prints.** For the file's date range:

- the keys the account is short of;
- any transactions in the account that are not in the file (manual entries,
  say, which a re-import leaves alone);
- how many rows a re-import would add.

It uses the same parser and planner as the import, so that prediction is what
an import would actually do. Nothing is written. On PostgreSQL it runs in a
`READ ONLY` transaction, and it works under the `books_readonly` login.

**Fixing an account it reports short:** re-import the same file through the
web UI. Only the missing rows are added.

#### Standard Django Commands

```bash
# Create a superuser
python manage.py createsuperuser

# Apply database migrations
python manage.py migrate

# Collect static files (production)
python manage.py collectstatic

# Create migrations after model changes
python manage.py makemigrations

# Run tests
python manage.py test
# or with pytest
pytest
```

---

## Mobile API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/m/` | GET | Mobile capture shell |
| `/m/api/time-entries/` | POST | Create time entry (JSON) |
| `/m/api/expenses/` | POST | Create expense (JSON) |
| `/m/api/meta/` | GET | Client/category metadata |
| `/m/time/` | GET | Mobile time entry list |
| `/m/expenses/` | GET | Mobile expense list |

---

## Production Deployment

### Requirements
- Ubuntu 22.04+ (or similar)
- Docker + Docker Compose
- Domain name with DNS configured

### Environment Variables

Create a `.env` file:

```bash
DJANGO_SECRET_KEY=your-secure-secret-key
DJANGO_DEBUG=False          # default; never set this to True in production
DJANGO_ALLOWED_HOSTS=your-domain.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://your-domain.com

POSTGRES_DB=ardua_books
POSTGRES_USER=ardua
POSTGRES_PASSWORD=strong-password
POSTGRES_HOST=db
POSTGRES_PORT=5432
```

### Deploy with Docker

```bash
# Initial deployment
docker compose up -d --build

# Run migrations
docker compose exec web python manage.py migrate

# Create superuser
docker compose exec web python manage.py createsuperuser

# Collect static files
docker compose exec web python manage.py collectstatic --noinput
```

### Running Management Commands in Production

```bash
# Import QuickBooks data
docker compose exec web python manage.py import_qb_invoices /path/to/file.csv \
    --income-account 4000 --consultant 1

# Clear transactional data
docker compose exec web python manage.py clear_transactions --yes

# Database backup (PostgreSQL)
docker compose exec db pg_dump -U ardua ardua_books > backup.sql
```

### Data Migration (SQLite → PostgreSQL)

When moving from local development (SQLite) to production (PostgreSQL), use the migration scripts to transfer your data.

#### Quick Migration

```bash
# Migrate metadata only (clients, accounts, categories, etc.)
./scripts/migrate_to_production.sh --metadata-only

# Migrate everything (metadata + all transactions)
./scripts/migrate_to_production.sh
```

The script automatically:
1. Exports data from local SQLite
2. Copies export to the Docker container
3. Imports into PostgreSQL
4. Cleans up temporary files

#### Manual Migration

If you need more control, use the management command directly:

**Export from local:**
```bash
# Metadata only
python manage.py migrate_data export --metadata-only --output ./data_export

# Everything
python manage.py migrate_data export --output ./data_export
```

**Import to production:**
```bash
# Copy export to container
docker cp ./data_export <container>:/app/data_export

# Run import
docker compose exec web python manage.py migrate_data import --input /app/data_export
```

#### What Gets Migrated

| Category | Models |
|----------|--------|
| **Metadata** | Users, Groups, Clients, Consultants, ExpenseCategories, ChartOfAccounts, BankAccounts, BankImportProfiles, RecurringCharges |
| **Transactions** | TimeEntries, Expenses, Invoices, InvoiceLines, RecurringChargeOccurrences, JournalEntries, JournalLines, Payments, PaymentApplications, BankTransactions |

#### Migration Tips

- **Migrate metadata first** with `--metadata-only`, verify it looks correct, then migrate transactions
- **Create a backup** of your production database before importing
- The import handles foreign key dependencies automatically by importing in the correct order
- If you need to re-migrate, use `clear_transactions` in production first to avoid duplicates

### NGINX

The `deploy/nginx.conf` provides a reverse proxy configuration. For production:
- Configure TLS with Let's Encrypt/Certbot
- Serve static files from the Docker volume
- Proxy application traffic to Gunicorn

### Read-only Database Access

`books_readonly` is a Postgres login for reporting and ad-hoc queries against
production. It can read the business data and nothing else.

**What it can and cannot do**

- `SELECT` on every table in `public`, except the ones that hold credentials:
  `django_session`, `socialaccount_socialtoken`, `socialaccount_socialapp`,
  `account_emailconfirmation`, and anything matching `mfa_%`,
  `usersessions_%` or `%token%`. Read access to those would let a
  "read-only" login hijack a live admin session or impersonate a user.
- `auth_user` is readable except the `password` column, so
  `SELECT * FROM auth_user` is denied: name the columns you want.
- No `INSERT`/`UPDATE`/`DELETE`/DDL. That is enforced by privileges; the
  role's `default_transaction_read_only` is only a convenience.
- Queries time out after 2 minutes, and a transaction left idle for 5 minutes
  is ended. An open transaction holds locks that would otherwise stall the
  next deploy's `migrate`, and every app query behind it.

**How it is maintained**

- `ardua_books/deploy/readonly_role.sql` defines the role, its grants and the
  denylist. It is idempotent, contains no secret, and the deploy workflow
  re-applies it after every `migrate`. New tables become readable (or stay
  denied) on the same deploy that creates them. Deliberately, there are no
  default privileges, so a new table is never readable before the denylist
  has seen it.
- Postgres is published on books' loopback only (`127.0.0.1:5432`). It is not
  reachable from the LAN; access is through SSH.

**Setting or rotating the password** (from homer)

```bash
python3 ardua_books/deploy/set_readonly_password.py
```

This generates a password, sends only its SCRAM-SHA-256 verifier to the
server (the plaintext never leaves your machine), and writes it to
`~/.pgpass` (mode 0600) plus an `[ardua-books-ro]` profile in
`~/.pg_service.conf`. It never prints the password. Run it again to rotate.

**Connecting with psql**

```bash
brew install libpq          # one-time; keg-only, so use the full path below
ssh -fN -L 15432:127.0.0.1:5432 jramsey@books.ardua.lan
/opt/homebrew/opt/libpq/bin/psql service=ardua-books-ro
```

**Connecting with DBeaver** (uses its own SSH tunnel; no `ssh -L` needed)

- *Main*: host `127.0.0.1`, port `5432`, database `ardua_books`,
  user `books_readonly`
- *SSH*: enable "Use SSH Tunnel": host `books.ardua.lan`, user `jramsey`,
  public key `~/.ssh/id_ed25519`
- To copy the password to the clipboard without displaying it:
  `awk -F: '$4=="books_readonly"{print $5}' ~/.pgpass | pbcopy`

---

## Testing

```bash
# Run all tests with pytest
cd ardua_books
pytest

# Run with coverage
pytest --cov=billing --cov=accounting

# Configuration guards (signup closed, login method, URL mounting)
pytest tests/

# Run specific test file
pytest billing/tests/test_models.py

# Run Django's test runner
python manage.py test
```

---

## Future Roadmap

- Credit card reconciliation
- Vendor payables module
- Balance sheet reporting
- Expense category → GL account mapping
- Improved offline support with background sync
- Export to external accounting systems

---

## License

Private / Proprietary

---

## Author

Ardua, Inc.
Jim Ramsey
