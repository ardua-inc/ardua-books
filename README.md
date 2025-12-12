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
- **Trial Balance** – Aggregated debits/credits across all accounts
- **Income Statement** – Revenue, expenses, and net income
- **AR Aging** – Overdue invoice analysis by aging buckets
- **Client Balance Summary** – Outstanding balances by client

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
│   ├── views.py              # CRUD views + PDF generation
│   ├── forms.py
│   ├── urls.py
│   └── templates/billing/
│
├── accounting/               # GL & reporting module
│   ├── models.py             # ChartOfAccount, JournalEntry, Payment, BankAccount
│   ├── services/
│   │   ├── posting.py        # GL posting/reversing
│   │   ├── banking.py        # Bank transaction logic
│   │   ├── importing.py      # CSV import utilities
│   │   └── payment_allocation.py
│   ├── views.py              # Payment, bank account, transaction views
│   ├── views_reports.py      # Trial balance, income statement, AR aging
│   ├── views_dashboard.py    # Reports home
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
python3 -m venv .venv
source .venv/bin/activate
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
docker compose up -d --build
```

### NGINX

The `deploy/nginx.conf` provides a reverse proxy configuration. For production:
- Configure TLS with Let's Encrypt/Certbot
- Serve static files from the Docker volume
- Proxy application traffic to Gunicorn

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
