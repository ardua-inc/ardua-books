# Bank CSV import: keep genuine same-day duplicates

Spec: ~/.hermes/profiles/claude/cache/scratch/ardua-books-import-dedupe-prompt.md
Branch: fix/import-keep-same-day-duplicates (not pushed to main; main deploys to production)

Deviations from the spec, decided up front:
- Version is 3.3.3, not 3.3.2: 3.3.2 already shipped (books_readonly), and production runs 3.3.2-7973587.
- The spec's amount cleaning, `replace('$','').replace(',','').strip()`, fails on its own example: `-$ 1,308.35` becomes `- 1308.35`, which Decimal rejects. All whitespace is removed instead.

- [x] Shared parser + count-based plan in `accounting/services/importing.py` (one implementation, used by both the view and the audit)
- [x] View: parse everything first, row-numbered errors, lock the account row, insert only the multiset difference
- [x] `audit_import_duplicates` management command (read-only; Postgres transaction set READ ONLY)
- [x] Tests: the 6 the spec asks for, plus amount, date and length validation, BOM handling, atomicity, and agreement between the audit and a real import
- [x] Full suite green
- [x] Run the audit against production via books_readonly with `~/Downloads/activity-7.csv`; check it against the spec's claim (6 Uber rows short)
- [x] Docs, CHANGELOG, VERSION 3.3.3; commit on the branch; push the branch only

## Review
- **Tests:** 124 passed, 42 of them new in `accounting/tests/test_csv_import.py`.
  - Run against the unmodified view from `main`, all 8 `TestDuplicateHandling` cases fail for the right reasons: it imports 1 of 2 identical rows and 1 of 3, raises `IndexError` on an empty date cell, and collapses the repeated charge.
- **Production audit:** run read-only through `books_readonly` with `activity-7.csv`, it reproduces the spec's claim exactly — 6 rows short, all Uber "TRIP HELP" (2/8 ×2, 2/9 ×2, 2/11, 2/21), $5.77 in total, and nothing in Ardua Books that isn't in the file.
- **Other accounts:**
  - Amex 2025, Chase 3876 2025 and Chase 6330 2026 are clean.
  - Chase 3876 2026 is short 25 rows, but all of them were never imported (the account was last imported in mid-April). None are collapsed repeats.
- **Spec deviations:**
  - Version is 3.3.3 rather than 3.3.2.
  - The amount cleaner strips all whitespace; the spec's formula fails on its own Citi example.
  - Rows with a non-zero third decimal place are rejected.
  - Descriptions over 255 characters are rejected.
  - Imports into the same account are serialised with a row lock.
  - The audit splits "short" into collapsed repeats and rows never imported.
  - The audit lists only mismatched keys by default; `--all` lists every key.

## Pre-merge verification against the latest exports (2026-11-01)
- Audited the 10-31 YTD exports in `~/Documents/Ardua/Statements/CreditCards` against production, read-only.
- **Found:** Amex re-padded 3 descriptions between exports (`AMAZON MARKEPLACE NA PA` → `…NA  PA`). With exact matching, a re-import would have double-booked them ($607.30), and the audit listed them as both short and extra.
- **Fixed before merging:** duplicate matching collapses whitespace runs in descriptions (`match_description`); stored descriptions keep the bank's spacing. 2 new tests; suite 126 passed.
- **After the fix:** Amex adds 109 / skips 341 with nothing extra; Chase 3876 adds 69 / skips 48; Chase 6330 adds 1 / skips 10. The new Amex file has exactly the spec's 5 repeat groups / 7 rows.
