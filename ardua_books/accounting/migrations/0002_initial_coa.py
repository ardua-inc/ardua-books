from django.db import migrations

def create_default_accounts(apps, schema_editor):
    ChartOfAccount = apps.get_model("accounting", "ChartOfAccount")
    ChartOfAccount.objects.update_or_create(
        code="1100", defaults={"name": "Accounts Receivable", "type": "ASSET"}
    )
    ChartOfAccount.objects.update_or_create(
        code="3000", defaults={"name": "Owner Equity", "type": "EQUITY"}
    )
    ChartOfAccount.objects.update_or_create(
        code="4000", defaults={"name": "Consulting Revenue", "type": "INCOME"}
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_default_accounts),
    ]

