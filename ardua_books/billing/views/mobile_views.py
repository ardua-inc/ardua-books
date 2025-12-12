"""
Mobile/PWA entry views for quick time and expense capture.
"""
import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from billing.models import (
    Client,
    Consultant,
    TimeEntry,
    Expense,
    ExpenseCategory,
)


@login_required
def mobile_home(request):
    """Render mobile shell template with PWA hooks."""
    return render(request, "billing/mobile_home.html", {"mobile": True})


@login_required
def mobile_time_list(request):
    """Simple recent time-entry list for mobile."""
    qs = (
        TimeEntry.objects
        .select_related("client", "consultant")
        .order_by("-work_date", "-created_at")
    )
    entries = qs[:50]

    return render(request, "billing/mobile_time_list.html", {
        "mobile": True,
        "time_entries": entries,
    })


@login_required
def mobile_expense_list(request):
    """Simple recent expense list for mobile."""
    qs = (
        Expense.objects
        .select_related("client", "category")
        .order_by("-expense_date", "-created_at")
    )
    expenses = qs[:50]

    return render(request, "billing/mobile_expense_list.html", {
        "mobile": True,
        "expenses": expenses,
    })


@login_required
@require_POST
def mobile_time_entry_create(request):
    """
    Create a TimeEntry from a JSON payload posted by the mobile PWA.

    Expected JSON:
      {
        "date": "YYYY-MM-DD",
        "hours": "4",
        "description": "Some text",
        "client_id": 123  # optional
      }
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload"}, status=400)

    work_date = parse_date(data.get("date") or "") or date.today()

    raw_hours = data.get("hours")
    try:
        hours = Decimal(str(raw_hours))
    except (InvalidOperation, TypeError):
        return JsonResponse({"error": f"Invalid hours value: {raw_hours!r}"}, status=400)

    description = (data.get("description") or "").strip()

    try:
        consultant = Consultant.objects.get(user=request.user)
    except Consultant.DoesNotExist:
        return JsonResponse(
            {"error": "No Consultant is linked to this user. Create one in admin."},
            status=400,
        )

    client_id = data.get("client_id")
    client = None
    if client_id:
        try:
            client = Client.objects.get(pk=client_id)
        except Client.DoesNotExist:
            return JsonResponse({"error": f"Client {client_id} not found."}, status=400)
    else:
        client = Client.objects.filter(is_active=True).order_by("name").first() or Client.objects.first()

    if not client:
        return JsonResponse(
            {"error": "No Client exists. Create a client first."},
            status=400,
        )

    billing_rate = (
        consultant.default_hourly_rate
        or client.default_hourly_rate
        or Decimal("0.00")
    )

    te = TimeEntry.objects.create(
        client=client,
        consultant=consultant,
        work_date=work_date,
        hours=hours,
        description=description,
        billing_rate=billing_rate,
    )

    return JsonResponse({"ok": True, "id": te.pk})


@login_required
@require_POST
def mobile_expense_create(request):
    """
    Create an Expense from a JSON payload posted by the mobile PWA.

    Expected JSON:
      {
        "date": "YYYY-MM-DD",
        "amount": "123.45",
        "description": "Some text",
        "client_id": 123,    # optional
        "category_id": 45    # optional
      }
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload"}, status=400)

    expense_date = parse_date(data.get("date") or "") or date.today()

    raw_amount = data.get("amount")
    try:
        amount = Decimal(str(raw_amount))
    except (InvalidOperation, TypeError):
        return JsonResponse({"error": f"Invalid amount value: {raw_amount!r}"}, status=400)

    description = (data.get("description") or "").strip()

    client_id = data.get("client_id")
    client = None
    if client_id:
        try:
            client = Client.objects.get(pk=client_id)
        except Client.DoesNotExist:
            return JsonResponse({"error": f"Client {client_id} not found."}, status=400)
    else:
        client = Client.objects.filter(is_active=True).order_by("name").first() or Client.objects.first()

    if not client:
        return JsonResponse(
            {"error": "No Client exists. Create a client first."},
            status=400,
        )

    category_id = data.get("category_id")
    category = None
    if category_id:
        try:
            category = ExpenseCategory.objects.get(pk=category_id)
        except ExpenseCategory.DoesNotExist:
            return JsonResponse({"error": f"Category {category_id} not found."}, status=400)
    else:
        category = ExpenseCategory.objects.order_by("name").first()

    if not category:
        return JsonResponse(
            {"error": "No ExpenseCategory exists. Create one first."},
            status=400,
        )

    expense = Expense.objects.create(
        client=client,
        category=category,
        expense_date=expense_date,
        amount=amount,
        description=description,
        billable=True,
    )

    return JsonResponse({"ok": True, "id": expense.pk})


@login_required
def mobile_meta(request):
    """
    Return metadata for the mobile app: active clients and expense categories.
    """
    clients = list(
        Client.objects
        .filter(is_active=True)
        .order_by("name")
        .values("id", "name")
    )
    categories = list(
        ExpenseCategory.objects
        .order_by("name")
        .values("id", "name")
    )
    return JsonResponse({"clients": clients, "categories": categories})
