"""
Tests for bank/card CSV import (BankTransactionCSVImportView) and the
read-only audit_import_duplicates command.

The behaviour under test: the duplicate check treats the file as a multiset.
Re-importing a file adds nothing, but genuinely repeated transactions -- two
identical charges on the same day, common on card statements -- are all kept.
Before 3.3.3 the importer collapsed them into one, silently understating the
account.

Tests go through the view (logged-in, non-Viewer user, real file upload) so
that parsing, planning, posting and messages are exercised together.
"""

from datetime import date
from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from accounting.models import (
    AccountType,
    BankAccountType,
    BankImportProfile,
    BankTransaction,
    JournalEntry,
)
from accounting.services.banking import BankTransactionService
from accounting.services.importing import parse_amount
from conftest import BankAccountFactory, ChartOfAccountFactory, UserFactory

AMEX_HEADER = "Date,Description,Card Member,Account #,Amount"


# --- fixtures and helpers -----------------------------------------------------

@pytest.fixture
def offset_account(db):
    return ChartOfAccountFactory(type=AccountType.EXPENSE)


def make_card(sign_rule, **profile_overrides):
    """A credit card account with an import profile. Defaults mirror the
    production Amex profile (date 0, description 1, amount 4)."""
    account = BankAccountFactory(
        type=BankAccountType.CREDIT_CARD,
        account=ChartOfAccountFactory(type=AccountType.LIABILITY),
    )
    settings = dict(
        date_column_index=0,
        description_column_index=1,
        amount_column_index=4,
        date_format="%m/%d/%Y",
        sign_rule=sign_rule,
    )
    settings.update(profile_overrides)
    BankImportProfile.objects.create(bank_account=account, **settings)
    return account


@pytest.fixture
def amex(db):
    return make_card("CC_CHARGES_POSITIVE")


@pytest.fixture
def logged_in(client, db):
    client.force_login(UserFactory())
    return client


def amex_csv(*rows):
    """rows: (date, description, amount) -> an Amex-shaped CSV with a header."""
    lines = [AMEX_HEADER]
    lines += [f"{d},{desc},JIM RAMSEY,-13008,{amt}" for d, desc, amt in rows]
    return "\n".join(lines) + "\n"


def upload(client, account, offset_account, content, *, raw_bytes=None):
    """POST the file and follow the redirect, as a browser would. Rendering the
    destination page consumes the flash messages, so one upload's messages
    cannot leak into the next request's."""
    data = raw_bytes if raw_bytes is not None else content.encode("utf-8")
    return client.post(
        reverse("accounting:banktransaction_import", args=[account.pk]),
        {
            "file": SimpleUploadedFile("activity.csv", data, content_type="text/csv"),
            "offset_account": offset_account.pk,
        },
        follow=True,
    )


def message_texts(response):
    return [str(m) for m in response.context["messages"]]


def landed_on_register(response, account):
    """Success redirects to the account register."""
    return response.status_code == 200 and response.redirect_chain == [
        (reverse("accounting:bankaccount_register", args=[account.pk]), 302)
    ]


def stayed_on_form(response):
    """A rejected file re-renders the import form; it is not a 500."""
    return response.status_code == 200 and response.redirect_chain == []


def stored(account):
    return list(
        BankTransaction.objects.filter(bank_account=account)
        .order_by("date", "id")
        .values_list("date", "description", "amount")
    )


ANTHROPIC = ("09/05/2026", "ANTHROPIC SAN FRANCISCO", "11.17")
UBER = ("02/09/2026", "Uber UBER *TRIP HELPSAO PAULO", "0.96")
COFFEE = ("01/15/2026", "BLUE BOTTLE COFFEE", "6.50")


# --- the six behaviours the fix was specified against -------------------------

class TestDuplicateHandling:
    def test_identical_rows_in_one_file_are_all_imported(self, logged_in, amex, offset_account):
        response = upload(logged_in, amex, offset_account, amex_csv(ANTHROPIC, ANTHROPIC))

        assert landed_on_register(response, amex)
        assert BankTransaction.objects.filter(bank_account=amex).count() == 2
        assert message_texts(response) == ["Imported 2 transaction(s)."]

    def test_reimporting_the_same_file_imports_nothing(self, logged_in, amex, offset_account):
        content = amex_csv(ANTHROPIC, ANTHROPIC)
        upload(logged_in, amex, offset_account, content)

        response = upload(logged_in, amex, offset_account, content)

        assert BankTransaction.objects.filter(bank_account=amex).count() == 2
        assert message_texts(response) == [
            "Imported 0 new transaction(s) (2 duplicate(s) skipped)."
        ]

    def test_one_existing_plus_three_identical_in_file_imports_two(
        self, logged_in, amex, offset_account
    ):
        # The state the old importer left behind: three charges collapsed to one.
        BankTransactionService.post_transaction(
            bank_account=amex,
            date=date(2026, 2, 9),
            description="Uber UBER *TRIP HELPSAO PAULO",
            amount=Decimal("-0.96"),
            offset_account=offset_account,
        )

        response = upload(logged_in, amex, offset_account, amex_csv(UBER, UBER, UBER))

        assert BankTransaction.objects.filter(bank_account=amex).count() == 3
        assert message_texts(response) == [
            "Imported 2 new transaction(s) (1 duplicate(s) skipped)."
        ]

    def test_grown_year_to_date_file_imports_only_the_new_rows(
        self, logged_in, amex, offset_account
    ):
        january = [COFFEE, COFFEE, ("01/20/2026", "AWS EMEA", "41.00")]
        february = [UBER, UBER, UBER, ("02/14/2026", "GITHUB", "4.00")]
        upload(logged_in, amex, offset_account, amex_csv(*january))

        response = upload(logged_in, amex, offset_account, amex_csv(*january, *february))

        assert BankTransaction.objects.filter(bank_account=amex).count() == 7
        assert message_texts(response) == [
            "Imported 4 new transaction(s) (3 duplicate(s) skipped)."
        ]

    def test_respaced_description_is_not_a_new_transaction(self, logged_in, amex, offset_account):
        # Amex re-padded these descriptions between two 2026 exports.
        upload(logged_in, amex, offset_account, amex_csv(("07/22/2026", "AMAZON MARKEPLACE NA PA", "55.30")))

        response = upload(
            logged_in, amex, offset_account,
            amex_csv(
                ("07/22/2026", "AMAZON MARKEPLACE NA  PA", "55.30"),
                ("07/22/2026", "AMAZON  MARKEPLACE NA PA ", "55.30"),
            ),
        )

        # One copy already present, the file has two: one more, kept as the bank spells it.
        assert stored(amex) == [
            (date(2026, 7, 22), "AMAZON MARKEPLACE NA PA", Decimal("-55.30")),
            (date(2026, 7, 22), "AMAZON MARKEPLACE NA  PA", Decimal("-55.30")),
        ]
        assert message_texts(response) == [
            "Imported 1 new transaction(s) (1 duplicate(s) skipped)."
        ]

    def test_blank_header_and_empty_date_rows_are_skipped(self, logged_in, amex, offset_account):
        content = "\n".join([
            AMEX_HEADER,
            "",
            ",,,,",
            "   ,   ,  ,  ,  ",
            ",PENDING AUTHORIZATION,JIM RAMSEY,-13008,3.00",   # empty date cell
            "Total,,,,58.33",                                  # summary line
            "Statement period",                                # one-column note
            "01/15/2026,BLUE BOTTLE COFFEE,JIM RAMSEY,-13008,6.50",
            "",
        ])

        response = upload(logged_in, amex, offset_account, content)

        assert landed_on_register(response, amex)
        assert stored(amex) == [
            (date(2026, 1, 15), "BLUE BOTTLE COFFEE", Decimal("-6.50"))
        ]

    def test_short_transaction_row_rejects_the_file_with_its_line_number(
        self, logged_in, amex, offset_account
    ):
        content = "\n".join([
            AMEX_HEADER,
            "01/15/2026,BLUE BOTTLE COFFEE,JIM RAMSEY,-13008,6.50",
            "01/16/2026,STARBUCKS",
        ])

        response = upload(logged_in, amex, offset_account, content)

        assert stayed_on_form(response)
        assert BankTransaction.objects.count() == 0  # the good row was not imported either
        (message,) = message_texts(response)
        assert "nothing was imported" in message
        assert "Line 3: expected at least 5 columns, found 2" in message

    @pytest.mark.parametrize(
        "sign_rule, columns, csv_charge, csv_payment",
        [
            # Amex: charges positive in the file, columns date/desc/amount = 0/1/4.
            ("CC_CHARGES_POSITIVE", dict(), "11.17", "-50.00"),
            # Chase: charges negative in the file, columns 0/2/5.
            ("CC_CHARGES_NEGATIVE", dict(description_column_index=2, amount_column_index=5),
             "-11.17", "50.00"),
        ],
    )
    def test_sign_rules_still_applied_and_reimport_stays_idempotent(
        self, logged_in, offset_account, sign_rule, columns, csv_charge, csv_payment
    ):
        account = make_card(sign_rule, **columns)
        width = max(columns.get("amount_column_index", 4), 2) + 1

        def row(date, desc, amount):
            cells = [""] * width
            cells[0] = date
            cells[columns.get("description_column_index", 1)] = desc
            cells[columns.get("amount_column_index", 4)] = amount
            return ",".join(cells)

        content = "\n".join([
            "header",
            row("03/01/2026", "OPENAI", csv_charge),
            row("03/01/2026", "OPENAI", csv_charge),
            row("03/02/2026", "AUTOPAY PAYMENT", csv_payment),
        ]) + "\n"

        upload(logged_in, account, offset_account, content)
        second = upload(logged_in, account, offset_account, content)

        # Internal convention: charges negative, payments positive.
        assert sorted(a for _, _, a in stored(account)) == [
            Decimal("-11.17"), Decimal("-11.17"), Decimal("50.00")
        ]
        # The key compares normalised amounts, so the re-import matched all 3.
        assert message_texts(second) == [
            "Imported 0 new transaction(s) (3 duplicate(s) skipped)."
        ]


# --- amounts, dates and other row validation ---------------------------------

class TestAmountParsing:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("11.17", Decimal("11.17")),
            (" 12.00 ", Decimal("12.00")),
            ("$1,308.35", Decimal("1308.35")),
            ("-$ 1,308.35", Decimal("-1308.35")),  # Citi
            ("-1,308.35", Decimal("-1308.35")),
            ("11.170", Decimal("11.17")),           # trailing zero is fine
            ("5", Decimal("5.00")),
        ],
    )
    def test_accepted_formats(self, raw, expected):
        assert parse_amount(raw) == expected

    @pytest.mark.parametrize(
        "raw, reason",
        [
            ("", "amount is empty"),
            ("  $ ", "amount is empty"),
            ("abc", "is not a number"),
            ("NaN", "is not a number"),
            ("Infinity", "is not a number"),
            ("1.005", "more than 2 decimal places"),
            ("10000000000.00", "too large"),
        ],
    )
    def test_rejected_values(self, raw, reason):
        with pytest.raises(ValueError, match=reason):
            parse_amount(raw)

    def test_formatted_amounts_import_through_the_view(self, logged_in, amex, offset_account):
        content = amex_csv(("01/02/2026", "HOTEL", '"$1,308.35"'), ("01/03/2026", "REFUND", '"-$ 1,308.35"'))

        upload(logged_in, amex, offset_account, content)

        assert [a for _, _, a in stored(amex)] == [Decimal("-1308.35"), Decimal("1308.35")]


class TestRejectedFiles:
    """Every rejection names the line, imports nothing, and is not a 500."""

    @pytest.mark.parametrize(
        "bad_row, expected",
        [
            ("2026-01-16,STARBUCKS,JIM RAMSEY,-13008,4.00",
             "Line 3: date '2026-01-16' does not match the format %m/%d/%Y"),
            ("01/16/2026,STARBUCKS,JIM RAMSEY,-13008,1.005",
             "Line 3: amount '1.005' has more than 2 decimal places"),
            ("01/16/2026,STARBUCKS,JIM RAMSEY,-13008,",
             "Line 3: amount is empty"),
            ("01/16/2026,STARBUCKS,JIM RAMSEY,-13008,four",
             "Line 3: amount 'four' is not a number"),
            ("01/16/2026," + "X" * 256 + ",JIM RAMSEY,-13008,4.00",
             "Line 3: description is 256 characters; the limit is 255"),
        ],
    )
    def test_bad_row_after_good_rows_rejects_whole_file(
        self, logged_in, amex, offset_account, bad_row, expected
    ):
        content = "\n".join([
            AMEX_HEADER,
            "01/15/2026,BLUE BOTTLE COFFEE,JIM RAMSEY,-13008,6.50",
            bad_row,
        ])

        response = upload(logged_in, amex, offset_account, content)

        assert stayed_on_form(response)
        assert BankTransaction.objects.count() == 0
        assert JournalEntry.objects.count() == 0
        (message,) = message_texts(response)
        assert expected in message

    def test_non_utf8_file_is_rejected(self, logged_in, amex, offset_account):
        response = upload(
            logged_in, amex, offset_account, None,
            raw_bytes="01/15/2026,CAFÉ,JIM RAMSEY,-13008,6.50\n".encode("latin-1"),
        )

        assert stayed_on_form(response)
        assert BankTransaction.objects.count() == 0
        (message,) = message_texts(response)
        assert "not UTF-8" in message


class TestOtherImportBehaviour:
    def test_byte_order_mark_does_not_hide_the_first_row(self, logged_in, amex, offset_account):
        # Header-less file (as Wells Fargo exports) saved with a BOM.
        content = "01/15/2026,BLUE BOTTLE COFFEE,JIM RAMSEY,-13008,6.50\n"

        upload(logged_in, amex, offset_account, None, raw_bytes=content.encode("utf-8-sig"))

        assert len(stored(amex)) == 1

    def test_skip_phrase_rows_are_not_counted_as_duplicates(self, logged_in, offset_account):
        account = make_card("CC_CHARGES_POSITIVE", skip_if_description_contains="autopay")
        content = amex_csv(COFFEE, ("01/20/2026", "AUTOPAY PAYMENT - THANK YOU", "-500.00"))

        first = upload(logged_in, account, offset_account, content)
        second = upload(logged_in, account, offset_account, content)

        assert [d for _, d, _ in stored(account)] == ["BLUE BOTTLE COFFEE"]
        assert message_texts(first) == ["Imported 1 transaction(s)."]
        assert message_texts(second) == [
            "Imported 0 new transaction(s) (1 duplicate(s) skipped)."
        ]

    def test_each_imported_transaction_is_posted_to_the_ledger(
        self, logged_in, amex, offset_account
    ):
        upload(logged_in, amex, offset_account, amex_csv(UBER, UBER, COFFEE))

        txn_type = ContentType.objects.get_for_model(BankTransaction)
        txn_ids = set(BankTransaction.objects.filter(bank_account=amex).values_list("id", flat=True))
        posted = set(
            JournalEntry.objects.filter(source_content_type=txn_type).values_list(
                "source_object_id", flat=True
            )
        )
        assert len(txn_ids) == 3
        assert posted == txn_ids

    def test_viewer_cannot_import(self, client, amex, offset_account):
        viewer = UserFactory()
        viewer.groups.add(Group.objects.get_or_create(name="Viewer")[0])
        client.force_login(viewer)

        response = upload(client, amex, offset_account, amex_csv(COFFEE))

        assert response.status_code == 403
        assert BankTransaction.objects.count() == 0


# --- audit_import_duplicates --------------------------------------------------

def run_audit(*args):
    out = StringIO()
    call_command("audit_import_duplicates", *map(str, args), stdout=out)
    return out.getvalue()


@pytest.fixture
def csv_file(tmp_path):
    def write(content):
        path = tmp_path / "activity.csv"
        path.write_text(content)
        return path
    return write


class TestAuditCommand:
    def test_reports_keys_short_in_the_database_and_writes_nothing(
        self, logged_in, amex, offset_account, csv_file
    ):
        # Simulate the pre-fix import: three Uber charges collapsed into one.
        upload(logged_in, amex, offset_account, amex_csv(UBER, COFFEE))
        path = csv_file(amex_csv(UBER, UBER, UBER, COFFEE))
        before = (BankTransaction.objects.count(), JournalEntry.objects.count())

        output = run_audit(amex.pk, path)

        assert (BankTransaction.objects.count(), JournalEntry.objects.count()) == before
        assert "Short in Ardua Books: 2 transaction(s) under 1 key(s)" in output
        assert "2 where some but not all copies are present" in output
        assert "0 where no copy is present (never imported)" in output
        assert "2026-02-09         -0.96     3     1     +2  Uber UBER *TRIP HELPSAO PAULO" in output
        assert "BLUE BOTTLE" not in output.split("Short in Ardua Books")[1].split("In Ardua Books")[0]
        assert "would add 2 transaction(s) and skip 2 as already present" in output
        assert "no changes were made" in output

    def test_prediction_matches_what_an_import_then_does(
        self, logged_in, amex, offset_account, csv_file
    ):
        upload(logged_in, amex, offset_account, amex_csv(UBER, COFFEE))
        content = amex_csv(UBER, UBER, UBER, COFFEE, COFFEE, ANTHROPIC)
        path = csv_file(content)

        predicted = run_audit(amex.pk, path)
        response = upload(logged_in, amex, offset_account, content)
        after = run_audit(amex.pk, path)

        assert "would add 4 transaction(s) and skip 2" in predicted
        assert message_texts(response) == [
            "Imported 4 new transaction(s) (2 duplicate(s) skipped)."
        ]
        assert "Short in Ardua Books: none." in after
        assert "would add 0 transaction(s) and skip 6" in after

    def test_separates_collapsed_repeats_from_rows_never_imported(
        self, logged_in, amex, offset_account, csv_file
    ):
        upload(logged_in, amex, offset_account, amex_csv(UBER))
        path = csv_file(amex_csv(UBER, UBER, COFFEE, COFFEE, COFFEE))

        output = run_audit(amex.pk, path)

        assert "Short in Ardua Books: 4 transaction(s) under 2 key(s)" in output
        assert "1 where some but not all copies are present" in output
        assert "3 where no copy is present (never imported)" in output

    def test_reports_transactions_in_the_database_but_not_the_file(
        self, logged_in, amex, offset_account, csv_file
    ):
        upload(logged_in, amex, offset_account, amex_csv(COFFEE, ("01/18/2026", "MANUAL ENTRY", "9.99")))
        path = csv_file(amex_csv(COFFEE, ("01/20/2026", "AWS", "41.00")))

        output = run_audit(amex.pk, path)

        assert "In Ardua Books but not in the file: 1 transaction(s) under 1 key(s)" in output
        assert "MANUAL ENTRY" in output

    def test_respaced_description_is_neither_short_nor_extra(
        self, logged_in, amex, offset_account, csv_file
    ):
        upload(logged_in, amex, offset_account, amex_csv(("05/02/2026", "AMAZON MARKEPLACE NA PA", "10.83")))
        path = csv_file(amex_csv(("05/02/2026", "AMAZON MARKEPLACE NA  PA", "10.83")))

        output = run_audit(amex.pk, path)

        assert "Short in Ardua Books: none." in output
        assert "In Ardua Books but not in the file: none." in output
        assert "would add 0 transaction(s) and skip 1" in output

    def test_all_flag_lists_every_key(self, logged_in, amex, offset_account, csv_file):
        upload(logged_in, amex, offset_account, amex_csv(COFFEE))
        path = csv_file(amex_csv(COFFEE, UBER))

        output = run_audit(amex.pk, path, "--all")

        assert "Every key" in output
        every_key = output.split("Every key")[1].split("Short in Ardua Books")[0]
        assert "BLUE BOTTLE COFFEE" in every_key and "Uber UBER" in every_key

    def test_unknown_account(self, db, csv_file):
        with pytest.raises(CommandError, match="No bank account with id 999999"):
            run_audit(999999, csv_file(amex_csv(COFFEE)))

    def test_account_without_profile(self, db, csv_file):
        account = BankAccountFactory()
        with pytest.raises(CommandError, match="has no import profile"):
            run_audit(account.pk, csv_file(amex_csv(COFFEE)))

    def test_bad_row_is_reported_with_its_line(self, amex, csv_file):
        path = csv_file(amex_csv(COFFEE) + "01/16/2026,STARBUCKS\n")
        with pytest.raises(CommandError, match="Line 3: expected at least 5 columns"):
            run_audit(amex.pk, path)

    def test_missing_file(self, amex, tmp_path):
        with pytest.raises(CommandError, match="Cannot read"):
            run_audit(amex.pk, tmp_path / "nope.csv")
