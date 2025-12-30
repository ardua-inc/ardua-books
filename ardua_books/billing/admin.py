from django.contrib import admin
from .models import (
    Company,
    Client,
    Consultant,
    ExpenseCategory,
    TimeEntry,
    Expense,
    Invoice,
    InvoiceLine,
)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone")

    def has_add_permission(self, request):
        # Only allow adding if no Company exists yet
        return not Company.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Prevent deletion of the company record
        return False


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "default_hourly_rate", "payment_terms_days", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "email")


@admin.register(Consultant)
class ConsultantAdmin(admin.ModelAdmin):
    list_display = ("display_name", "user", "default_hourly_rate")
    search_fields = ("display_name", "user__username")


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "billable_by_default")


@admin.register(TimeEntry)
class TimeEntryAdmin(admin.ModelAdmin):
    list_display = ("work_date", "client", "consultant", "hours", "billing_rate", "status")
    list_filter = ("client", "consultant", "status")
    search_fields = ("description",)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("expense_date", "client", "category", "amount", "billable", "status")
    list_filter = ("category", "billable", "status")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "client", "issue_date", "due_date", "status", "total")
    list_filter = ("status", "client")


@admin.register(InvoiceLine)
class InvoiceLineAdmin(admin.ModelAdmin):
    list_display = ("invoice", "line_type", "description", "quantity", "unit_price", "line_total")
    list_filter = ("line_type",)
