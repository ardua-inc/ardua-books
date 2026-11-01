"""
Compare a bank/card CSV with what Ardua Books already holds for that account.

Read-only. Built for checking imports made before 3.3.3: those collapsed
genuinely repeated transactions (two identical charges on the same day) into
one, leaving the account understated.

For every (date, description, amount) key it compares how many times the key
appears in the file with how many matching transactions the account has, over
the file's date range. It then reports what re-importing the file would do,
using the same parser and planner as the import view, so the prediction
matches what an import would actually do.

Usage:
    python manage.py audit_import_duplicates <bank_account_id> <csv_path>
    python manage.py audit_import_duplicates 2 activity.csv --all   # every key

Nothing is written. On PostgreSQL the work runs in a READ ONLY transaction,
so a write would be refused by the database rather than merely not attempted.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from accounting.models import BankAccount, BankImportProfile
from accounting.services.importing import (
    decode_csv,
    parse_bank_csv,
    plan_import,
)


class Command(BaseCommand):
    help = "Read-only: compare a bank CSV with the account's transactions, per key"

    def add_arguments(self, parser):
        parser.add_argument("bank_account_id", type=int)
        parser.add_argument("csv_path", type=Path)
        parser.add_argument(
            "--all",
            action="store_true",
            help="List every key, not only the ones whose counts differ.",
        )

    def handle(self, *args, **options):
        path = options["csv_path"]
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise CommandError(f"Cannot read {path}: {exc.strerror}")

        with transaction.atomic():
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
            try:
                self._audit(options["bank_account_id"], path, data, options["all"])
            finally:
                # Belt and braces on databases without READ ONLY transactions.
                transaction.set_rollback(True)

    def _audit(self, account_id, path, data, list_all):
        try:
            account = BankAccount.objects.get(pk=account_id)
        except BankAccount.DoesNotExist:
            raise CommandError(f"No bank account with id {account_id}.")
        try:
            profile = account.import_profile
        except BankImportProfile.DoesNotExist:
            raise CommandError(f"Bank account {account_id} has no import profile.")

        try:
            rows = parse_bank_csv(decode_csv(data), profile)
        except ValueError as exc:  # includes CSVImportError
            raise CommandError(f"{path.name}: {exc}")
        if not rows:
            raise CommandError(f"{path.name}: no transaction rows found.")

        plan = plan_import(account, rows)
        file_counts, db_counts = plan.file_counts, plan.db_counts
        first, last = min(r.date for r in rows), max(r.date for r in rows)
        extra = db_counts - file_counts  # in the database, not in the file

        out = self.stdout.write
        out(f"Bank account {account.pk}: {account.institution} {account.account_number_masked}")
        out(f"File: {path.name}, {len(rows)} transaction rows, {first} to {last}")
        out(f"Ardua Books, same date range: {sum(db_counts.values())} transactions")
        out("Amounts use Ardua Books' sign: charges negative, payments positive.")
        out("")

        if list_all:
            keys = sorted(set(file_counts) | set(db_counts))
            self._table("Every key", keys, file_counts, db_counts)

        short = plan.short_keys
        if short:
            # A key the account holds some, but not all, copies of is the
            # signature of the pre-3.3.3 bug (repeats collapsed into one). A key
            # with no copies at all was simply never imported.
            collapsed = {k: n for k, n in short.items() if db_counts[k]}
            absent = {k: n for k, n in short.items() if not db_counts[k]}
            self._table(
                f"Short in Ardua Books: {sum(short.values())} transaction(s) "
                f"under {len(short)} key(s)\n"
                f"  {sum(collapsed.values())} where some but not all copies are present "
                f"(repeats collapsed by imports before 3.3.3)\n"
                f"  {sum(absent.values())} where no copy is present (never imported)",
                sorted(short), file_counts, db_counts,
            )
        else:
            out("Short in Ardua Books: none.")
            out("")

        if extra:
            self._table(
                f"In Ardua Books but not in the file: {sum(extra.values())} "
                f"transaction(s) under {len(extra)} key(s). A re-import leaves "
                f"these alone; they may be manual entries or a different export.",
                sorted(extra), file_counts, db_counts,
            )
        else:
            out("In Ardua Books but not in the file: none.")
            out("")

        out(
            f"A re-import of this file would add {len(plan.to_insert)} "
            f"transaction(s) and skip {plan.duplicate_count} as already present."
        )
        out(self.style.SUCCESS("Read-only audit: no changes were made."))

    def _table(self, title, keys, file_counts, db_counts):
        out = self.stdout.write
        out(title)
        out(f"  {'date':<10}  {'amount':>12}  {'file':>4}  {'db':>4}  {'diff':>5}  description")
        for date_, description, amount in keys:
            key = (date_, description, amount)
            in_file, in_db = file_counts[key], db_counts[key]
            out(
                f"  {date_.isoformat():<10}  {amount:>12}  {in_file:>4}  {in_db:>4}  "
                f"{in_file - in_db:>+5}  {description}"
            )
        out("")
