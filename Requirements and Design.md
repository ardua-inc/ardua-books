# Ardua Books

## Requirements and Design Document

---

## 1. Overview

Ardua Books is a lightweight, integrated billing and accounting platform designed for an independent consultant or a very small consulting firm. The system provides:

- Client management
- Time entry and expense capture with receipt uploads
- Invoice generation with PDF output
- General ledger accounting with automatic posting
- Payment tracking and allocation
- Bank account management with transaction import
- Financial reporting (Trial Balance, Income Statement, AR Aging)
- Mobile/PWA quick-entry interface

The system is **not** intended to be a full ERP or multi-entity accounting system, but is architected to evolve and support additional accounting functions over time.

---

## 2. Functional Requirements

### 2.1 Client Management

Users can create, update, and list clients. Each client record stores:

| Field | Description |
|-------|-------------|
| name | Unique client identifier |
| billing_address | Full billing address (text) |
| email | Contact email |
| phone | Contact phone |
| default_hourly_rate | Default rate for time entries |
| payment_terms_days | Net payment terms (e.g., 30 days) |
| is_active | Active/inactive flag |

**Behavior:**
- List views show active clients by default
- Option to include inactive clients in listings
- Client name must be unique

### 2.2 Consultant Management

Each Django `User` may have an associated `Consultant` record:

| Field | Description |
|-------|-------------|
| user | One-to-one link to Django User |
| display_name | Name shown on reports/invoices |
| default_hourly_rate | Optional rate override |

Consultant records are used for time entry attribution and rate defaults.

### 2.3 Time Entry Management

Time entries represent billable work performed.

| Field | Description |
|-------|-------------|
| client | Associated client |
| consultant | Who performed the work |
| work_date | Date work was performed |
| hours | Number of hours worked |
| description | Work description |
| billing_rate | Rate at time of entry (snapshot) |
| status | UNBILLED, BILLED, or WRITTEN_OFF |
| invoice_line | Link to InvoiceLine when billed |

**Behavior:**
- Billing rate defaults from client, then consultant if not set
- Only UNBILLED entries can be attached to invoices
- Status changes to BILLED when invoice is issued
- List view shows latest entries first

### 2.4 Expense Management

Expenses represent reimbursable or non-reimbursable costs.

| Field | Description |
|-------|-------------|
| client | Optional (required if billable) |
| category | ExpenseCategory reference |
| expense_date | Date of expense |
| amount | Expense amount |
| description | Expense description |
| billable | Whether expense is billable |
| status | UNBILLED, BILLED, or WRITTEN_OFF |
| receipt | File upload (image or PDF) |
| invoice_line | Link to InvoiceLine when billed |

**Behavior:**
- Billable expenses must have a client assigned
- If saved as billable without client, automatically set to non-billable
- Receipts stored in date-organized directories (`receipts/%Y/%m/%d/`)
- Only billable, UNBILLED expenses can be attached to invoices

### 2.5 Expense Categories

| Field | Description |
|-------|-------------|
| name | Category name (unique) |
| billable_by_default | Default billable flag for new expenses |

### 2.6 Invoice Management

Invoices aggregate billable time and expenses for a client.

| Field | Description |
|-------|-------------|
| client | Associated client |
| invoice_number | Auto-generated (YYYY-NNN format) |
| sequence | Per-year sequence counter |
| issue_date | Date invoice was issued |
| due_date | Payment due date |
| status | DRAFT, ISSUED, PAID, or VOID |
| notes | Additional invoice notes |
| subtotal | Cached subtotal amount |
| tax_amount | Cached tax amount |
| total | Cached total amount |

**Constraints:**
- Only one DRAFT invoice per client at any time
- Invoice number auto-generates with new sequence each year
- Due date defaults to issue_date + client.payment_terms_days
- Cached totals recalculated when lines change

### 2.7 Invoice Lines

Each invoice contains one or more line items:

| Field | Description |
|-------|-------------|
| invoice | Parent invoice |
| line_type | TIME, EXPENSE, ADJUSTMENT, or GENERAL |
| description | Line item description |
| quantity | Quantity (hours for time, 1 for expenses) |
| unit_price | Rate or amount |
| line_total | Auto-calculated (quantity x unit_price) |

**Behavior:**
- TIME lines link back to TimeEntry records
- EXPENSE lines link back to Expense records
- Line total automatically computed on save
- Lines ordered by creation time

### 2.8 Attaching Unbilled Items

From invoice edit, users select unbilled time and expenses to attach:

**Selection Criteria:**
- Same client as invoice
- Status is UNBILLED
- Not already attached to any invoice

**Attachment Process:**
- TIME line created: quantity = hours, unit_price = billing_rate
- EXPENSE line created: quantity = 1, unit_price = amount
- Source item links to new InvoiceLine via `invoice_line` field
- Invoice totals recalculated immediately

### 2.9 Invoice Status Transitions

| Transition | Actions |
|------------|---------|
| DRAFT → ISSUED | Attached items marked BILLED; GL entry created (DR A/R, CR Revenue) |
| ISSUED → VOID | GL reversing entry created; items may revert to UNBILLED |
| ISSUED → DRAFT | GL reversing entry created; allows line editing |
| Delete DRAFT | Attached items detached and reverted to UNBILLED |

### 2.10 PDF Generation

The system generates:

1. **Print-friendly HTML** – Invoice with header, lines, totals
2. **Combined PDF** – Invoice PDF with receipt pages appended
   - Invoice rendered via WeasyPrint
   - PDF receipts appended directly
   - Image receipts rendered to PDF pages and appended

---

## 3. Accounting Requirements

### 3.1 Chart of Accounts

The system maintains a chart of accounts with five account types:

| Type | Normal Balance | Examples |
|------|----------------|----------|
| ASSET | Debit | Cash, Accounts Receivable |
| LIABILITY | Credit | Accounts Payable |
| EQUITY | Credit | Owner Equity |
| INCOME | Credit | Consulting Revenue |
| EXPENSE | Debit | Operating Expenses |

**Default Accounts (created via migration):**

| Code | Name | Type |
|------|------|------|
| 1000 | Cash | Asset |
| 1100 | Accounts Receivable | Asset |
| 3000 | Owner Equity | Equity |
| 4000 | Consulting Revenue | Income |

### 3.2 Journal Entries

Journal entries record all financial transactions:

| Field | Description |
|-------|-------------|
| posted_at | Timestamp of posting |
| posted_by | User who created entry |
| description | Transaction description |
| content_type | Generic FK type (Invoice, Payment, etc.) |
| object_id | Generic FK ID |

**Journal Lines:**

| Field | Description |
|-------|-------------|
| entry | Parent JournalEntry |
| account | ChartOfAccount reference |
| debit | Debit amount |
| credit | Credit amount |

**Constraints:**
- Total debits must equal total credits per entry
- All posting/reversing wrapped in `transaction.atomic()`

### 3.3 Automatic Posting Rules

**Invoice Issued (DRAFT → ISSUED):**
```
Dr Accounts Receivable (1100)    [invoice total]
    Cr Consulting Revenue (4000) [invoice total]
```

**Invoice Voided/Reverted (ISSUED → VOID/DRAFT):**
```
Dr Consulting Revenue (4000)     [invoice total]
    Cr Accounts Receivable (1100) [invoice total]
```

**Duplicate Prevention:**
- System checks count of JournalEntries for document
- Odd count = currently posted
- Even count = currently reversed
- Prevents duplicate postings

### 3.4 Payment Management

Payments record money received from clients:

| Field | Description |
|-------|-------------|
| client | Client making payment |
| date | Payment date |
| amount | Total payment amount |
| method | CHECK, ACH, CASH, CARD, or OTHER |
| memo | Payment reference/notes |
| unapplied_amount | Portion not yet allocated |

**Payment Applications:**

| Field | Description |
|-------|-------------|
| payment | Parent Payment |
| invoice | Invoice receiving payment |
| amount | Amount applied to this invoice |

**GL Entry on Payment:**
```
Dr Cash (1000)                   [total amount]
    Cr Accounts Receivable (1100) [applied amount]
    Cr Unapplied Payments         [unapplied amount]
```

### 3.5 Bank Account Management

Bank accounts track cash positions:

| Field | Description |
|-------|-------------|
| account | One-to-one link to ChartOfAccount |
| type | CHECKING, SAVINGS, CREDIT_CARD, or CASH |
| institution | Bank/institution name |
| account_number_masked | Last 4 digits for display |
| opening_balance | Starting balance |

**Balance Calculation:**
- Computed from GL entries against linked ChartOfAccount
- Opening balance + sum of debits - sum of credits

### 3.6 Bank Transactions

Individual transactions within bank accounts:

| Field | Description |
|-------|-------------|
| bank_account | Parent BankAccount |
| date | Transaction date |
| description | Transaction description |
| amount | Positive = deposit, negative = withdrawal |
| journal_entry | Link to GL entry when posted |
| offset_account | Offset account for GL posting |
| payment | Link to Payment if matched |

### 3.7 Bank Import

CSV import with configurable mapping:

| Field | Description |
|-------|-------------|
| bank_account | Account for import |
| date_column_index | CSV column for date |
| description_column_index | CSV column for description |
| amount_column_index | CSV column for amount |
| date_format | Python strptime format |
| sign_rule | How to interpret amounts |
| skip_if_description_contains | Filter unwanted transactions |

**Sign Rules:**
- AS_IS: Use amount as provided
- NEGATE: Flip sign
- SEPARATE_COLUMNS: Debit/credit in different columns

---

## 4. Reporting Requirements

### 4.1 Trial Balance

- Lists all accounts with aggregated debit and credit totals
- Computes net balance per account
- Verifies total debits = total credits
- Filterable by date range

### 4.2 Income Statement

- Lists INCOME and EXPENSE accounts only
- Computes total revenue
- Computes total expenses
- Calculates net income (revenue - expenses)
- Filterable by date range

### 4.3 AR Aging Report

- Lists all outstanding (ISSUED, unpaid) invoices
- Groups by aging buckets: Current, 1-30, 31-60, 61-90, 90+ days
- Shows invoice details and amounts
- Summarizes totals by bucket

### 4.4 Client Balance Summary

- Lists all clients with outstanding balances
- Shows total invoiced, total paid, balance due
- Sorted by balance amount

---

## 5. Mobile/PWA Requirements

### 5.1 Mobile Entry Shell

Available at `/m/`, optimized for small screens:

- "New Time Entry" button
- "New Expense" button
- Quick access to recent entries

### 5.2 Mobile API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/m/api/time-entries/` | POST | Create time entry (JSON) |
| `/m/api/expenses/` | POST | Create expense (JSON) |
| `/m/api/meta/` | GET | Retrieve client/category lists |

**Authentication:**
- Session-based login (same as desktop)
- CSRF token required via `X-CSRFToken` header

### 5.3 PWA Features

- **Web App Manifest** – App name, icons, theme colors, start URL
- **Service Worker** – Caches shell, JS, CSS, manifest for offline access
- **Add to Home Screen** – Supported on iOS and Android
- **Icons** – 192px and 512px PNG icons

---

## 6. Domain Model

### 6.1 Entity Relationships

```
Client (1) ─────────────── (*) TimeEntry
   │                              │
   │                              │ invoice_line
   │                              ▼
   ├──────────────────── (*) InvoiceLine ◄─── (*) Expense
   │                              │
   │                              │ invoice
   │                              ▼
   └─────────────────────── (*) Invoice
                                  │
                                  │ (generic FK)
                                  ▼
                            JournalEntry ◄─── Payment
                                  │
                                  ▼
                            (*) JournalLine ──── ChartOfAccount
                                                       │
                                                       │ (1:1)
                                                       ▼
                                                  BankAccount
                                                       │
                                                       ▼
                                              (*) BankTransaction
```

### 6.2 Key Relationships

| Relationship | Type | Description |
|--------------|------|-------------|
| Client → TimeEntry | 1:N | Client has many time entries |
| Client → Expense | 1:N | Client has many expenses (if billable) |
| Client → Invoice | 1:N | Client has many invoices |
| Invoice → InvoiceLine | 1:N | Invoice has many lines |
| TimeEntry → InvoiceLine | 1:1 | Time entry links to one line |
| Expense → InvoiceLine | 1:1 | Expense links to one line |
| Invoice → JournalEntry | 1:N | Invoice may have multiple entries (post/reverse) |
| Payment → JournalEntry | 1:1 | Payment creates one GL entry |
| Payment → PaymentApplication | 1:N | Payment allocated to multiple invoices |
| ChartOfAccount → BankAccount | 1:1 | Bank account links to GL account |
| BankAccount → BankTransaction | 1:N | Account has many transactions |

---

## 7. Technical Architecture

### 7.1 Application Layers

```
┌─────────────────────────────────────────────────────────┐
│                    Presentation Layer                    │
│  Django Templates │ Mobile PWA │ PDF Generation          │
├─────────────────────────────────────────────────────────┤
│                    Application Layer                     │
│  Views (CRUD) │ Forms │ URL Routing                      │
├─────────────────────────────────────────────────────────┤
│                    Business Logic Layer                  │
│  billing/services.py │ accounting/services/              │
├─────────────────────────────────────────────────────────┤
│                    Data Access Layer                     │
│  Django ORM │ Models │ Migrations                        │
├─────────────────────────────────────────────────────────┤
│                    Infrastructure                        │
│  PostgreSQL/SQLite │ File Storage │ Static Files         │
└─────────────────────────────────────────────────────────┘
```

### 7.2 Module Organization

**billing/** – Operational billing and receivables
- `models.py` – Client, Consultant, TimeEntry, Expense, Invoice, InvoiceLine
- `services.py` – Invoice number generation, item attachment/detachment
- `views.py` – CRUD views, PDF generation, mobile endpoints
- `forms.py` – Django forms for data entry
- `templatetags/` – Custom template filters

**accounting/** – General ledger and financial reporting
- `models.py` – ChartOfAccount, JournalEntry, JournalLine, Payment, BankAccount, BankTransaction
- `services/posting.py` – GL posting and reversing logic
- `services/banking.py` – Bank transaction operations
- `services/importing.py` – CSV import utilities
- `services/payment_allocation.py` – Payment formset logic
- `views.py` – Payment, bank account, transaction management
- `views_reports.py` – Trial balance, income statement, AR aging
- `views_dashboard.py` – Reports navigation hub

### 7.3 Key Business Logic

**Invoice Number Generation** (`billing/services.py`):
- Format: YYYY-NNN (e.g., 2024-001)
- Sequence resets each calendar year
- Finds max sequence for current year, increments

**Item Attachment** (`billing/services.py`):
- Creates InvoiceLine for each selected item
- Links source item to line via `invoice_line` FK
- Recalculates invoice totals

**GL Posting** (`accounting/services/posting.py`):
- Creates JournalEntry with balanced lines
- Uses generic FK to link to source document
- Wrapped in atomic transaction
- Idempotency via entry count check

**Payment Allocation** (`accounting/services/payment_allocation.py`):
- Distributes payment across selected invoices
- Tracks unapplied remainder
- Updates invoice paid status

---

## 8. Security Design

### 8.1 Authentication

- Django built-in authentication system
- Session-based login with secure cookies
- Login required for all billing/accounting views
- Admin access restricted to staff users

### 8.2 Authorization

- Currently coarse-grained: any authenticated user can access all data
- Admin operations via Django admin restricted to staff
- Future: per-consultant scoping, read-only roles

### 8.3 CSRF Protection

- Enabled and enforced for all unsafe HTTP methods
- Mobile JS sends `X-CSRFToken` header from cookie
- Django middleware validates on all POST/PUT/DELETE

### 8.4 File Upload Security

- Receipts stored outside web root in MEDIA_ROOT
- Served through Django (dev) or NGINX (prod)
- File type validation on upload

### 8.5 Transport Security

- Production deployment requires HTTPS
- CSRF_TRUSTED_ORIGINS configured for domain
- Secure cookie flags in production

---

## 9. Deployment Architecture

### 9.1 Development Environment

- Django development server (`runserver`)
- SQLite database
- Local file storage for media
- Static files served by Django

### 9.2 Production Environment

```
                    ┌─────────────┐
                    │   NGINX     │
                    │ (TLS/Proxy) │
                    └──────┬──────┘
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
       ┌─────────────┐          ┌─────────────┐
       │   Static    │          │   Gunicorn  │
       │   Files     │          │   (WSGI)    │
       └─────────────┘          └──────┬──────┘
                                       │
                                       ▼
                                ┌─────────────┐
                                │   Django    │
                                │   App       │
                                └──────┬──────┘
                                       │
                    ┌──────────────────┴──────────────────┐
                    │                                     │
                    ▼                                     ▼
             ┌─────────────┐                      ┌─────────────┐
             │ PostgreSQL  │                      │   Media     │
             │  Database   │                      │   Storage   │
             └─────────────┘                      └─────────────┘
```

### 9.3 Docker Services

| Service | Image | Purpose |
|---------|-------|---------|
| web | Custom (Dockerfile) | Django + Gunicorn |
| db | postgres:15 | PostgreSQL database |

**Volumes:**
- `db_data` – PostgreSQL data persistence
- `static_volume` – Collected static files
- `media_volume` – Uploaded media files

### 9.4 WeasyPrint Dependencies

System libraries required for PDF generation:
- Cairo
- Pango
- GDK-PixBuf
- libffi

Installed in Dockerfile for production builds.

---

## 10. Non-Functional Requirements

### 10.1 Performance

- Designed for small dataset (single consultant/small firm)
- Query optimization for invoice and report views
- Cached totals on Invoice model to avoid repeated calculation
- Pagination on list views as needed

### 10.2 Usability

- Desktop UI: Clear tables, straightforward forms
- Mobile UI: Minimal taps for quick entry
- PDF output: Professional appearance suitable for clients

### 10.3 Maintainability

- Django conventions throughout
- Clear separation: models, forms, views, templates
- Business logic isolated in services modules
- Migrations track all schema changes

### 10.4 Reliability

- Atomic transactions for multi-step operations
- Idempotent posting logic prevents duplicates
- Validation at model and form levels

---

## 11. Future Roadmap

### 11.1 Near Term

- Credit card account reconciliation
- Vendor payables module
- Balance sheet report

### 11.2 Medium Term

- Expense category → GL account mapping
- Automatic expense GL posting
- Enhanced audit trail

### 11.3 Long Term

- Improved offline support with background sync
- Multi-currency support
- Export to external accounting systems
- API for third-party integrations
