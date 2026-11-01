"""
Bank / card CSV import: parsing, sign normalisation and duplicate planning.

Used by BankTransactionCSVImportView and by the audit_import_duplicates
management command. Keeping both on this one implementation is what makes the
audit's "a re-import would add N rows" prediction trustworthy.
"""

import csv
import io
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db.models import Count

from accounting.models import BankTransaction

CENT = Decimal("0.01")

# BankTransaction.amount is DecimalField(max_digits=12, decimal_places=2).
MAX_ABS_AMOUNT = Decimal("9999999999.99")


def normalize_amount(raw_amount: Decimal, profile):
    """
    Convert CSV amount into the internal Ardua Books convention:
    + = payment/deposit
    - = charge/withdrawal
    """

    rule = profile.sign_rule

    if rule == "BANK_STANDARD":
        # Bank accounts: CSV convention matches internal convention
        return raw_amount

    elif rule == "CC_CHARGES_POSITIVE":
        # CSV: + charge, - payment
        # Internal: + payment, - charge
        return -raw_amount

    elif rule == "CC_CHARGES_NEGATIVE":
        # CSV: + payment, - charge
        return raw_amount

    return raw_amount


class CSVImportError(ValueError):
    """A row the importer cannot use. The whole import is rejected."""

    def __init__(self, line, reason):
        self.line = line
        self.reason = reason
        super().__init__(f"Line {line}: {reason}")


@dataclass(frozen=True)
class ParsedRow:
    line: int  # physical line number in the file, for error messages
    date: date
    description: str
    amount: Decimal  # internal sign convention, exactly 2 decimal places

    @property
    def key(self):
        """What makes two transactions indistinguishable to the importer."""
        return (self.date, match_description(self.description), self.amount)


def match_description(description):
    """
    A description as the duplicate check compares it: whitespace runs collapsed
    to a single space. The stored description keeps the bank's spacing.

    Banks re-pad descriptions between exports. Amex's 2026 export changed
    "AMAZON MARKEPLACE NA PA" to "AMAZON MARKEPLACE NA  PA" for transactions
    already imported; compared exactly, a re-import would book them again.
    """
    return " ".join(description.split())


def parse_amount(raw):
    """
    Parse an amount cell, tolerating a currency symbol, thousands separators
    and whitespace anywhere in the cell -- e.g. Citi's "-$ 1,308.35".

    Raises ValueError with a human-readable reason.
    """
    cleaned = "".join(raw.replace("$", "").replace(",", "").split())
    if not cleaned:
        raise ValueError("amount is empty")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"amount {raw.strip()!r} is not a number") from None
    if not amount.is_finite():
        raise ValueError(f"amount {raw.strip()!r} is not a number")
    # The column stores 2 decimal places. A value with a non-zero third
    # decimal would be rounded on save, and the stored row would then never
    # match the file again -- so every re-import would add it once more.
    if amount != amount.quantize(CENT):
        raise ValueError(f"amount {raw.strip()!r} has more than 2 decimal places")
    if abs(amount) > MAX_ABS_AMOUNT:
        raise ValueError(f"amount {raw.strip()!r} is too large")
    return amount.quantize(CENT)


def decode_csv(data):
    """
    Decode an uploaded CSV file.

    utf-8-sig drops a byte-order mark. Left in place it is glued to the first
    cell, so a header-less file's first date would not start with a digit and
    its first transaction would be silently skipped.

    Raises ValueError with a human-readable reason.
    """
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(
            "The file is not UTF-8 text. Export it from the bank as CSV."
        ) from None


def parse_bank_csv(text, profile):
    """
    Parse a decoded CSV file into ParsedRows, in file order.

    Rows are skipped, not rejected, when they are blank, when their date cell
    is empty or does not start with a digit (headers, summary lines), or when
    their description contains the profile's skip phrase.

    Any other row that cannot be used raises CSVImportError naming its line, so
    the caller can reject the whole file rather than import part of it.
    """
    date_idx = profile.date_column_index
    desc_idx = profile.description_column_index
    amount_idx = profile.amount_column_index
    min_columns = max(date_idx, desc_idx, amount_idx) + 1
    max_description = _description_max_length()

    reader = csv.reader(io.StringIO(text, newline=""))
    rows = []
    for row in reader:
        line = reader.line_num

        if not any(cell.strip() for cell in row):
            continue

        raw_date = row[date_idx].strip() if len(row) > date_idx else ""
        if not raw_date or not raw_date[0].isdigit():
            continue  # header, summary or other non-transaction row

        if len(row) < min_columns:
            raise CSVImportError(
                line, f"expected at least {min_columns} columns, found {len(row)}"
            )

        description = row[desc_idx].strip()
        if profile.skip_if_description_contains and (
            profile.skip_if_description_contains.lower() in description.lower()
        ):
            continue

        if len(description) > max_description:
            raise CSVImportError(
                line,
                f"description is {len(description)} characters; the limit is {max_description}",
            )

        try:
            parsed_date = datetime.strptime(raw_date, profile.date_format).date()
        except ValueError:
            raise CSVImportError(
                line, f"date {raw_date!r} does not match the format {profile.date_format}"
            ) from None

        try:
            amount = parse_amount(row[amount_idx])
        except ValueError as exc:
            raise CSVImportError(line, str(exc)) from None

        rows.append(
            ParsedRow(
                line=line,
                date=parsed_date,
                description=description,
                amount=normalize_amount(amount, profile),
            )
        )
    return rows


@dataclass
class ImportPlan:
    rows: list  # every parsed row, in file order
    file_counts: Counter  # key -> occurrences in the file
    db_counts: Counter  # key -> existing transactions in the account
    to_insert: list  # the rows to create, in file order

    @property
    def duplicate_count(self):
        return len(self.rows) - len(self.to_insert)

    @property
    def short_keys(self):
        """Keys the file has more of than the database: {key: missing}."""
        return {
            key: count - self.db_counts[key]
            for key, count in self.file_counts.items()
            if count > self.db_counts[key]
        }


def existing_counts(bank_account, rows):
    """Count existing transactions per key, over the file's date range."""
    if not rows:
        return Counter()

    dates = [row.date for row in rows]
    queryset = (
        BankTransaction.objects.filter(
            bank_account=bank_account,
            date__gte=min(dates),
            date__lte=max(dates),
        )
        .values("date", "description", "amount")
        .annotate(n=Count("id"))
    )
    # Descriptions differing only in spacing are one key, so sum, don't assign.
    counts = Counter()
    for item in queryset:
        key = (
            item["date"],
            match_description(item["description"]),
            item["amount"].quantize(CENT),
        )
        counts[key] += item["n"]
    return counts


def plan_import(bank_account, rows):
    """
    Decide which parsed rows to insert, treating the file as a multiset.

    For each key the file contributes count_in_file - count_in_db rows (never
    negative). Re-importing a file therefore adds nothing, while genuinely
    repeated transactions -- two identical charges on the same day -- are all
    kept, and a year-to-date file that has grown adds only the new rows.

    Rows with the same key are indistinguishable, so which of them are
    inserted does not matter; file order is kept.
    """
    file_counts = Counter(row.key for row in rows)
    db_counts = existing_counts(bank_account, rows)
    remaining = {
        key: max(count - db_counts[key], 0) for key, count in file_counts.items()
    }

    to_insert = []
    for row in rows:
        if remaining[row.key]:
            remaining[row.key] -= 1
            to_insert.append(row)

    return ImportPlan(
        rows=rows, file_counts=file_counts, db_counts=db_counts, to_insert=to_insert
    )


def _description_max_length():
    return BankTransaction._meta.get_field("description").max_length
