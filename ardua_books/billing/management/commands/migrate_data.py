"""
Data migration command for moving data between SQLite (dev) and PostgreSQL (prod).

Usage:
    # Export metadata only
    python manage.py migrate_data export --metadata-only --output /path/to/export

    # Export everything
    python manage.py migrate_data export --output /path/to/export

    # Import metadata only
    python manage.py migrate_data import --metadata-only --input /path/to/export

    # Import everything
    python manage.py migrate_data import --input /path/to/export

    # Import, skipping records that already exist (useful for production DBs
    # that have pre-populated tables like contenttypes or chart of accounts)
    python manage.py migrate_data import --skip-existing --input /path/to/export
"""
import json
import os
from django.core.management.base import BaseCommand, CommandError
from django.core import serializers
from django.contrib.contenttypes.models import ContentType


# Models grouped by dependency order
METADATA_MODELS = [
    # Django auth (no dependencies)
    'auth.Group',
    'auth.User',
    # Billing metadata (no FK dependencies)
    'billing.Client',
    'billing.Consultant',
    # Accounting metadata (ChartOfAccount must come before ExpenseCategory)
    'accounting.ChartOfAccount',
    'accounting.BankAccount',
    'accounting.BankImportProfile',
    # ExpenseCategory has FK to ChartOfAccount
    'billing.ExpenseCategory',
]

TRANSACTIONAL_MODELS = [
    # Invoices first (InvoiceLine is referenced by TimeEntry/Expense)
    'billing.Invoice',
    'billing.InvoiceLine',
    # Time and expenses (may have FK to InvoiceLine)
    'billing.TimeEntry',
    'billing.Expense',
    # Journal entries (referenced by BankTransaction)
    'accounting.JournalEntry',
    'accounting.JournalLine',
    # Payments (referenced by BankTransaction)
    'accounting.Payment',
    'accounting.PaymentApplication',
    # Bank transactions (depend on JournalEntry, Expense, Payment)
    'accounting.BankTransaction',
]


class Command(BaseCommand):
    help = "Export or import data for migration between databases"

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            choices=['export', 'import'],
            help='Action to perform: export or import',
        )
        parser.add_argument(
            '--metadata-only',
            action='store_true',
            help='Only migrate metadata (clients, accounts, categories, etc.)',
        )
        parser.add_argument(
            '--output',
            '-o',
            help='Output directory for export',
        )
        parser.add_argument(
            '--input',
            '-i',
            help='Input directory for import',
        )
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            help='Skip records that already exist (by primary key) instead of failing',
        )

    def handle(self, *args, **options):
        action = options['action']
        metadata_only = options['metadata_only']
        skip_existing = options['skip_existing']

        if action == 'export':
            output_dir = options.get('output')
            if not output_dir:
                raise CommandError("--output directory is required for export")
            self.export_data(output_dir, metadata_only)

        elif action == 'import':
            input_dir = options.get('input')
            if not input_dir:
                raise CommandError("--input directory is required for import")
            self.import_data(input_dir, metadata_only, skip_existing)

    def export_data(self, output_dir, metadata_only):
        """Export data to JSON files."""
        from django.apps import apps

        os.makedirs(output_dir, exist_ok=True)

        models_to_export = METADATA_MODELS.copy()
        if not metadata_only:
            models_to_export.extend(TRANSACTIONAL_MODELS)

        self.stdout.write(self.style.SUCCESS(f"\nExporting to: {output_dir}"))
        self.stdout.write("-" * 50)

        # First, export ContentTypes (needed for GenericForeignKey references)
        self.stdout.write("Exporting contenttypes...")
        cts = ContentType.objects.all()
        ct_data = serializers.serialize('json', cts, indent=2)
        with open(os.path.join(output_dir, '00_contenttypes.json'), 'w') as f:
            f.write(ct_data)
        self.stdout.write(f"  Exported {len(cts)} content types")

        for idx, model_label in enumerate(models_to_export, start=1):
            app_label, model_name = model_label.split('.')
            try:
                model = apps.get_model(app_label, model_name)
            except LookupError:
                self.stdout.write(
                    self.style.WARNING(f"  Model {model_label} not found, skipping")
                )
                continue

            queryset = model.objects.all()
            count = queryset.count()

            if count == 0:
                self.stdout.write(f"  {model_label}: 0 records (skipping)")
                continue

            # Serialize to JSON
            data = serializers.serialize('json', queryset, indent=2)
            filename = f"{idx:02d}_{app_label}_{model_name}.json"
            filepath = os.path.join(output_dir, filename)

            with open(filepath, 'w') as f:
                f.write(data)

            self.stdout.write(f"  {model_label}: {count} records -> {filename}")

        self.stdout.write("-" * 50)
        self.stdout.write(self.style.SUCCESS("Export complete!"))

        # Write manifest
        manifest = {
            'metadata_only': metadata_only,
            'models': models_to_export,
        }
        with open(os.path.join(output_dir, 'manifest.json'), 'w') as f:
            json.dump(manifest, f, indent=2)

    def import_data(self, input_dir, metadata_only, skip_existing=False):
        """Import data from JSON files."""
        from django.apps import apps
        from django.db import transaction, IntegrityError

        if not os.path.exists(input_dir):
            raise CommandError(f"Input directory does not exist: {input_dir}")

        # Read manifest
        manifest_path = os.path.join(input_dir, 'manifest.json')
        if os.path.exists(manifest_path):
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
            self.stdout.write(f"Found manifest: metadata_only={manifest.get('metadata_only')}")
        else:
            self.stdout.write(self.style.WARNING("No manifest found, proceeding anyway"))

        self.stdout.write(self.style.SUCCESS(f"\nImporting from: {input_dir}"))
        if skip_existing:
            self.stdout.write(self.style.WARNING("Mode: Skip existing records"))
        self.stdout.write("-" * 50)

        # Get all JSON files, sorted by name (which preserves dependency order)
        json_files = sorted([
            f for f in os.listdir(input_dir)
            if f.endswith('.json') and f != 'manifest.json'
        ])

        # Always skip contenttypes - Django auto-creates these and PKs differ between databases
        json_files = [f for f in json_files if 'contenttype' not in f.lower()]

        if metadata_only:
            # Filter to only metadata files
            metadata_prefixes = set()
            for idx, model_label in enumerate(METADATA_MODELS, start=1):
                app_label, model_name = model_label.split('.')
                metadata_prefixes.add(f"{idx:02d}_{app_label}_{model_name}")

            json_files = [
                f for f in json_files
                if any(f.startswith(p) for p in metadata_prefixes)
            ]

        total_imported = 0
        total_skipped = 0

        for filename in json_files:
            filepath = os.path.join(input_dir, filename)

            with open(filepath, 'r') as f:
                data = f.read()

            try:
                # Deserialize objects
                objects = list(serializers.deserialize('json', data))
                count = 0
                skipped = 0

                for obj in objects:
                    model_class = obj.object.__class__
                    pk = obj.object.pk

                    if skip_existing:
                        # Check if this record already exists by PK
                        if pk is not None and model_class.objects.filter(pk=pk).exists():
                            skipped += 1
                            continue

                    # Try to save, handling unique constraint violations
                    try:
                        with transaction.atomic():
                            obj.save()
                        count += 1
                    except IntegrityError as e:
                        if skip_existing:
                            # Unique constraint violation - skip this record
                            skipped += 1
                        else:
                            raise

                if skipped > 0:
                    self.stdout.write(
                        f"  {filename}: {count} imported, "
                        f"{self.style.WARNING(f'{skipped} skipped (existing)')}"
                    )
                else:
                    self.stdout.write(f"  {filename}: {count} records imported")

                total_imported += count
                total_skipped += skipped

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"  {filename}: ERROR - {e}")
                )
                raise

        self.stdout.write("-" * 50)
        summary = f"Import complete! {total_imported} records imported"
        if total_skipped > 0:
            summary += f", {total_skipped} skipped"
        self.stdout.write(self.style.SUCCESS(summary))
