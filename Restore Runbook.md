# Backup & Restore Runbook — Ardua Books

This runbook describes how to restore **database** and **receipt files** from S3 backups.

---

## Preconditions

* AWS CLI configured with access to `ardua-books-backups`
* Docker and docker-compose installed
* PostgreSQL version compatible with backup (Postgres 15)
* Target database exists (empty or disposable)

---

## Part A — Restore PostgreSQL Database

### 1. Identify the backup to restore

List available backups:

```bash
aws s3 ls s3://ardua-books-backups/postgres/last/
```

Example output:

```
2025-12-18 01:03:12  ardua_books-20251218-000312.dump
```

Choose the appropriate timestamped dump.

---

### 2. Download the backup

```bash
aws s3 cp \
  s3://ardua-books-backups/postgres/last/ardua_books-20251218-000312.dump \
  ./ardua_books.restore.dump
```

Verify file size is non-zero:

```bash
ls -lh ardua_books.restore.dump
```

---

### 3. Restore into a target database

Run `pg_restore` using a temporary container:

```bash
docker run --rm -it \
  -v "$PWD:/restore" \
  postgres:15 \
  pg_restore \
    --clean \
    --if-exists \
    --no-owner \
    --dbname=postgresql://USER:PASSWORD@HOST:5432/ardua_books \
    /restore/ardua_books.restore.dump
```

Replace:

* `USER`
* `PASSWORD`
* `HOST`

---

### 4. Post-restore checks

* Verify tables exist
* Start application container
* Confirm login and basic navigation

---

## Part B — Restore Receipts / Media Files

### 1. Restore receipts to a target directory

```bash
rclone sync \
  s3:ardua-books-backups/receipts \
  /restore/receipts \
  --checksum --progress
```

This recreates the full directory tree exactly.

---

### 2. Reattach to application (if needed)

If restoring into production:

```bash
rsync -a /restore/receipts/ /opt/ardua_books/media/receipts/
```

Restart the web container if required.

---

## Notes

* `*-latest.dump` files are local symlinks and are **not** authoritative
* Always restore from timestamped `.dump` files
* S3 mirrors local retention exactly

---

**Restore confidence check:**
If the database loads cleanly and receipts appear correctly, the restore is complete.

