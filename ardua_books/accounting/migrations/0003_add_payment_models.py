
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0001_initial'),
        ('accounting', '0002_initial_coa'),
    ]

    operations = [
        migrations.CreateModel(
            name='Payment',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('date', models.DateField(default=django.utils.timezone.now)),
                ('amount', models.DecimalField(max_digits=10, decimal_places=2)),
                ('method', models.CharField(max_length=20)),
                ('memo', models.CharField(max_length=255, blank=True)),
                ('unapplied_amount', models.DecimalField(max_digits=10, decimal_places=2, default=0)),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='billing.client')),
            ],
        ),
        migrations.CreateModel(
            name='PaymentApplication',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('amount', models.DecimalField(max_digits=10, decimal_places=2)),
                ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='billing.invoice')),
                ('payment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='applications', to='accounting.payment')),
            ],
        ),
    ]
