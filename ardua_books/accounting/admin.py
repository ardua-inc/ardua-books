from django.contrib import admin
from .models import ChartOfAccount, JournalEntry, JournalLine, BankAccount, BankTransaction, BankImportProfile


@admin.register(ChartOfAccount)
class ChartOfAccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "type", "is_active")
    list_filter = ("type", "is_active")
    search_fields = ("code", "name")


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    inlines = [JournalLineInline]
    list_display = ("id", "posted_at", "posted_by", "description")


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ("account", "institution", "account_number_masked", "type")
    list_filter = ("type",)

@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = ("bank_account", "date", "description", "amount")
    list_filter = ("bank_account", "date")
    search_fields = ("description",)

@admin.register(BankImportProfile)
class BankImportProfileAdmin(admin.ModelAdmin):
    list_display = (
        "bank_account",
        "sign_rule",
        "date_format",
    )
    search_fields = ("bank_account__institution", "bank_account__account_number_masked")