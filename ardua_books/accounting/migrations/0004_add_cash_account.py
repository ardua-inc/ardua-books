
from django.db import migrations

def create_cash_account(apps, schema_editor):
    COA = apps.get_model('accounting', 'ChartOfAccount')
    if not COA.objects.filter(code='1000').exists():
        COA.objects.create(code='1000', name='Cash', type='ASSET')

class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0003_add_payment_models'),
    ]

    operations = [
        migrations.RunPython(create_cash_account),
    ]
