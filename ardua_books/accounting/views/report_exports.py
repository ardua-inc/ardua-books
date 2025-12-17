"""
Export views for accounting reports.
Provides print-ready HTML, PDF download, and CSV export for each report.
"""
import csv
import io
from datetime import date, timedelta
from decimal import Decimal

import weasyprint

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Case, When, F, DecimalField, Q
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse

from accounting.models import ChartOfAccount, JournalLine, JournalEntry, AccountType, Payment, PaymentApplication
from billing.models import Client, Invoice


def get_date_range(request):
    """Parse date range from request parameters."""
    today = date.today()
    date_preset = request.GET.get("date_preset", "")
    date_from_str = request.GET.get("date_from", "")
    date_to_str = request.GET.get("date_to", "")

    if date_preset == "mtd":
        from_date = today.replace(day=1)
        to_date = today
    elif date_preset == "ytd":
        from_date = today.replace(month=1, day=1)
        to_date = today
    elif date_preset == "last_month":
        first_of_month = today.replace(day=1)
        last_month_end = first_of_month - timedelta(days=1)
        from_date = last_month_end.replace(day=1)
        to_date = last_month_end
    elif date_preset == "last_year":
        from_date = date(today.year - 1, 1, 1)
        to_date = date(today.year - 1, 12, 31)
    elif date_from_str or date_to_str:
        from_date = date.fromisoformat(date_from_str) if date_from_str else None
        to_date = date.fromisoformat(date_to_str) if date_to_str else None
    else:
        from_date = None
        to_date = None

    return from_date, to_date


def format_date_range(from_date, to_date):
    """Format date range for display."""
    if from_date and to_date:
        return f"{from_date.strftime('%b %d, %Y')} - {to_date.strftime('%b %d, %Y')}"
    elif from_date:
        return f"From {from_date.strftime('%b %d, %Y')}"
    elif to_date:
        return f"Through {to_date.strftime('%b %d, %Y')}"
    return "All Time"


def get_trial_balance_data(from_date, to_date):
    """Get trial balance report data."""
    date_filter = Q()
    if from_date:
        date_filter &= Q(journalline__entry__posted_at__date__gte=from_date)
    if to_date:
        date_filter &= Q(journalline__entry__posted_at__date__lte=to_date)

    accounts = (
        ChartOfAccount.objects.all()
        .annotate(
            debit_sum=Sum(
                Case(
                    When(date_filter & Q(journalline__debit__gt=0), then=F("journalline__debit")),
                    default=0,
                    output_field=DecimalField(),
                )
            ),
            credit_sum=Sum(
                Case(
                    When(date_filter & Q(journalline__credit__gt=0), then=F("journalline__credit")),
                    default=0,
                    output_field=DecimalField(),
                )
            ),
        )
        .order_by("type", "code")
    )

    for acct in accounts:
        acct.debit_sum = acct.debit_sum or Decimal("0")
        acct.credit_sum = acct.credit_sum or Decimal("0")
        acct.balance = acct.debit_sum - acct.credit_sum

    total_debits = sum(a.debit_sum for a in accounts)
    total_credits = sum(a.credit_sum for a in accounts)

    return {
        "accounts": list(accounts),
        "total_debits": total_debits,
        "total_credits": total_credits,
    }


def get_income_statement_data(from_date, to_date):
    """Get income statement report data."""
    date_filter = Q()
    if from_date:
        date_filter &= Q(journalline__entry__posted_at__date__gte=from_date)
    if to_date:
        date_filter &= Q(journalline__entry__posted_at__date__lte=to_date)

    accounts = (
        ChartOfAccount.objects.filter(
            type__in=[AccountType.INCOME, AccountType.EXPENSE]
        )
        .annotate(
            debit_sum=Sum(
                Case(
                    When(date_filter & Q(journalline__debit__gt=0), then=F("journalline__debit")),
                    default=0,
                    output_field=DecimalField(),
                )
            ),
            credit_sum=Sum(
                Case(
                    When(date_filter & Q(journalline__credit__gt=0), then=F("journalline__credit")),
                    default=0,
                    output_field=DecimalField(),
                )
            ),
        )
        .order_by("type", "code")
    )

    for a in accounts:
        raw_balance = (a.debit_sum or 0) - (a.credit_sum or 0)
        if a.type == AccountType.INCOME:
            a.balance = -raw_balance
        else:
            a.balance = raw_balance

    revenue_accounts = [a for a in accounts if a.type == AccountType.INCOME]
    expense_accounts = [a for a in accounts if a.type == AccountType.EXPENSE]

    revenue_total = sum(a.balance for a in revenue_accounts)
    expense_total = sum(a.balance for a in expense_accounts)
    net_income = revenue_total - expense_total

    return {
        "revenue_accounts": revenue_accounts,
        "expense_accounts": expense_accounts,
        "revenue_total": revenue_total,
        "expense_total": expense_total,
        "net_income": net_income,
    }


def get_client_balance_data():
    """Get client balance summary data."""
    rows = []
    for client in Client.objects.all().order_by("name"):
        invoices = Invoice.objects.filter(client=client)
        payments = Payment.objects.filter(client=client)

        total_invoiced = sum(inv.total for inv in invoices)
        outstanding = sum(inv.outstanding_balance() for inv in invoices)
        applied = sum(
            app.amount
            for app in PaymentApplication.objects.filter(invoice__client=client)
        )
        unapplied = sum(p.unapplied_amount for p in payments)

        rows.append({
            "client": client,
            "total_invoiced": total_invoiced,
            "applied": applied,
            "unapplied": unapplied,
            "outstanding": outstanding,
            "net_ar": outstanding - unapplied,
        })

    return rows


def get_journal_entries_data(from_date, to_date):
    """Get journal entries data."""
    entries = JournalEntry.objects.all().prefetch_related("lines", "lines__account")

    if from_date:
        entries = entries.filter(posted_at__date__gte=from_date)
    if to_date:
        entries = entries.filter(posted_at__date__lte=to_date)

    return entries.order_by("-posted_at", "-id")


# ==============================================================================
# TRIAL BALANCE EXPORTS
# ==============================================================================

@login_required
def trial_balance_print(request):
    """Print-ready HTML view of trial balance."""
    from_date, to_date = get_date_range(request)
    data = get_trial_balance_data(from_date, to_date)

    return render(request, "accounting/exports/trial_balance_print.html", {
        "report_title": "Trial Balance",
        "date_range": format_date_range(from_date, to_date),
        "back_url": reverse("accounting:trial_balance") + "?" + request.GET.urlencode(),
        **data,
    })


@login_required
def trial_balance_pdf(request):
    """Generate PDF of trial balance."""
    from_date, to_date = get_date_range(request)
    data = get_trial_balance_data(from_date, to_date)

    html = render_to_string("accounting/exports/trial_balance_print.html", {
        "report_title": "Trial Balance",
        "date_range": format_date_range(from_date, to_date),
        "back_url": "",
        **data,
    }, request=request)

    pdf = weasyprint.HTML(
        string=html,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf()

    response = HttpResponse(pdf, content_type="application/pdf")
    filename = f"trial-balance-{date.today().isoformat()}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def trial_balance_csv(request):
    """Export trial balance as CSV."""
    from_date, to_date = get_date_range(request)
    data = get_trial_balance_data(from_date, to_date)

    response = HttpResponse(content_type="text/csv")
    filename = f"trial-balance-{date.today().isoformat()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(["Trial Balance", format_date_range(from_date, to_date)])
    writer.writerow([])
    writer.writerow(["Account Code", "Account Name", "Type", "Debit", "Credit", "Balance"])

    for acct in data["accounts"]:
        writer.writerow([
            acct.code,
            acct.name,
            acct.get_type_display(),
            float(acct.debit_sum),
            float(acct.credit_sum),
            float(acct.balance),
        ])

    writer.writerow([])
    writer.writerow(["", "", "TOTALS", float(data["total_debits"]), float(data["total_credits"]), ""])

    return response


# ==============================================================================
# INCOME STATEMENT EXPORTS
# ==============================================================================

@login_required
def income_statement_print(request):
    """Print-ready HTML view of income statement."""
    from_date, to_date = get_date_range(request)
    data = get_income_statement_data(from_date, to_date)

    return render(request, "accounting/exports/income_statement_print.html", {
        "report_title": "Income Statement",
        "date_range": format_date_range(from_date, to_date),
        "back_url": reverse("accounting:income_statement") + "?" + request.GET.urlencode(),
        **data,
    })


@login_required
def income_statement_pdf(request):
    """Generate PDF of income statement."""
    from_date, to_date = get_date_range(request)
    data = get_income_statement_data(from_date, to_date)

    html = render_to_string("accounting/exports/income_statement_print.html", {
        "report_title": "Income Statement",
        "date_range": format_date_range(from_date, to_date),
        "back_url": "",
        **data,
    }, request=request)

    pdf = weasyprint.HTML(
        string=html,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf()

    response = HttpResponse(pdf, content_type="application/pdf")
    filename = f"income-statement-{date.today().isoformat()}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def income_statement_csv(request):
    """Export income statement as CSV."""
    from_date, to_date = get_date_range(request)
    data = get_income_statement_data(from_date, to_date)

    response = HttpResponse(content_type="text/csv")
    filename = f"income-statement-{date.today().isoformat()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(["Income Statement", format_date_range(from_date, to_date)])
    writer.writerow([])

    writer.writerow(["REVENUE"])
    writer.writerow(["Account Code", "Account Name", "Amount"])
    for acct in data["revenue_accounts"]:
        writer.writerow([acct.code, acct.name, float(acct.balance)])
    writer.writerow(["", "Total Revenue", float(data["revenue_total"])])

    writer.writerow([])
    writer.writerow(["EXPENSES"])
    writer.writerow(["Account Code", "Account Name", "Amount"])
    for acct in data["expense_accounts"]:
        writer.writerow([acct.code, acct.name, float(acct.balance)])
    writer.writerow(["", "Total Expenses", float(data["expense_total"])])

    writer.writerow([])
    writer.writerow(["", "NET INCOME", float(data["net_income"])])

    return response


# ==============================================================================
# CLIENT BALANCE SUMMARY EXPORTS
# ==============================================================================

@login_required
def client_balance_print(request):
    """Print-ready HTML view of client balance summary."""
    data = get_client_balance_data()

    return render(request, "accounting/exports/client_balance_print.html", {
        "report_title": "Client Balance Summary",
        "date_range": f"As of {date.today().strftime('%b %d, %Y')}",
        "back_url": reverse("accounting:client_balance_summary"),
        "summary": data,
    })


@login_required
def client_balance_pdf(request):
    """Generate PDF of client balance summary."""
    data = get_client_balance_data()

    html = render_to_string("accounting/exports/client_balance_print.html", {
        "report_title": "Client Balance Summary",
        "date_range": f"As of {date.today().strftime('%b %d, %Y')}",
        "back_url": "",
        "summary": data,
    }, request=request)

    pdf = weasyprint.HTML(
        string=html,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf()

    response = HttpResponse(pdf, content_type="application/pdf")
    filename = f"client-balance-summary-{date.today().isoformat()}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def client_balance_csv(request):
    """Export client balance summary as CSV."""
    data = get_client_balance_data()

    response = HttpResponse(content_type="text/csv")
    filename = f"client-balance-summary-{date.today().isoformat()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(["Client Balance Summary", f"As of {date.today().strftime('%b %d, %Y')}"])
    writer.writerow([])
    writer.writerow(["Client", "Total Invoiced", "Applied", "Unapplied", "Outstanding", "Net AR"])

    for row in data:
        writer.writerow([
            row["client"].name,
            float(row["total_invoiced"]),
            float(row["applied"]),
            float(row["unapplied"]),
            float(row["outstanding"]),
            float(row["net_ar"]),
        ])

    return response


# ==============================================================================
# JOURNAL ENTRIES EXPORTS
# ==============================================================================

@login_required
def journal_entries_print(request):
    """Print-ready HTML view of journal entries."""
    from_date, to_date = get_date_range(request)
    entries = get_journal_entries_data(from_date, to_date)

    return render(request, "accounting/exports/journal_entries_print.html", {
        "report_title": "Journal Entries",
        "date_range": format_date_range(from_date, to_date),
        "back_url": reverse("accounting:journal_list") + "?" + request.GET.urlencode(),
        "entries": entries,
    })


@login_required
def journal_entries_pdf(request):
    """Generate PDF of journal entries."""
    from_date, to_date = get_date_range(request)
    entries = get_journal_entries_data(from_date, to_date)

    html = render_to_string("accounting/exports/journal_entries_print.html", {
        "report_title": "Journal Entries",
        "date_range": format_date_range(from_date, to_date),
        "back_url": "",
        "entries": entries,
    }, request=request)

    pdf = weasyprint.HTML(
        string=html,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf()

    response = HttpResponse(pdf, content_type="application/pdf")
    filename = f"journal-entries-{date.today().isoformat()}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def journal_entries_csv(request):
    """Export journal entries as CSV."""
    from_date, to_date = get_date_range(request)
    entries = get_journal_entries_data(from_date, to_date)

    response = HttpResponse(content_type="text/csv")
    filename = f"journal-entries-{date.today().isoformat()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(["Journal Entries", format_date_range(from_date, to_date)])
    writer.writerow([])
    writer.writerow(["Entry ID", "Date", "Description", "Account Code", "Account Name", "Debit", "Credit"])

    for entry in entries:
        first_line = True
        for line in entry.lines.all():
            if first_line:
                writer.writerow([
                    entry.id,
                    entry.posted_at.strftime("%Y-%m-%d"),
                    entry.description or "",
                    line.account.code,
                    line.account.name,
                    float(line.debit) if line.debit else "",
                    float(line.credit) if line.credit else "",
                ])
                first_line = False
            else:
                writer.writerow([
                    "",
                    "",
                    "",
                    line.account.code,
                    line.account.name,
                    float(line.debit) if line.debit else "",
                    float(line.credit) if line.credit else "",
                ])

    return response
