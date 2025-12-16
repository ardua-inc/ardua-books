# Ardua Books

Ardua Books is a lightweight billing, invoicing, and accounting system designed for an independent consultant or small consulting firm.

---

## Features

### Billing & Invoicing
- **Client Management** – Contact info, default rates, payment terms, active/inactive status
- **Time Entry Tracking** – Billable hours with consultant attribution and rate snapshots
- **Expense Tracking** – Categorized expenses with receipt uploads (images/PDFs)
- **Invoice Generation** – Auto-numbered invoices (YYYY-NNN format) with status workflow
- **PDF Generation** – Professional invoice PDFs with attached receipt pages

### Accounting
- **Chart of Accounts** – Asset, Liability, Equity, Income, Expense account types
- **Journal Entries** – Automatic GL posting when invoices are issued/voided
- **Payment Tracking** – Record payments, allocate to invoices, track unapplied amounts
- **Bank Accounts** – Track checking, savings, credit card, and cash accounts
- **Bank Transaction Import** – CSV import with configurable column mapping and sign rules

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
| Framework | Django 5.x |
| Database | SQLite (dev), PostgreSQL (production) |
| PDF Generation | WeasyPrint + pypdf |
| Authentication | Django sessions + CSRF |
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
│   ├── models.py             # Client, Consultant, TimeEntry, Expense, Invoice
│   ├── services.py           # Invoice generation, item attachment
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
├── requirements.txt
└── manage.py
```

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
pip install -r requirements.txt
```

### 3. Apply migrations and create superuser

```bash
cd ardua_books
python manage.py migrate
python manage.py createsuperuser
```

### 4. Run development server

```bash
python manage.py runserver 8000
```

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

**Deletes:**
- Journal entries & lines
- Time entries
- Expenses
- Invoices & invoice lines
- Payments & payment applications
- Bank transactions

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
DJANGO_DEBUG=False
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

### NGINX

The `deploy/nginx.conf` provides a reverse proxy configuration. For production:
- Configure TLS with Let's Encrypt/Certbot
- Serve static files from the Docker volume
- Proxy application traffic to Gunicorn

---

## Testing

```bash
# Run all tests with pytest
cd ardua_books
pytest

# Run with coverage
pytest --cov=billing --cov=accounting

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
