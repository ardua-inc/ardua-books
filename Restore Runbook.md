# Backup & Restore Runbook — Ardua Books

This runbook describes how to restore **database** and **receipt/media files** from S3 backups, and how to verify backup health using sentinels.

---

## Preconditions

* AWS CLI configured with access to `ardua-books-backups`
* Docker and docker-compose installed
* PostgreSQL version compatible with backup (Postgres 15)
* Target database exists (empty or disposable)

---

## Part A — Verify Backup Health Before Restoring

### 1. Check upload sentinels (pipeline health)

```bash
aws s3 cp s3://ardua-books-backups/_sentinels/postgres.last_success -
aws s3 cp s3://ardua-books-backups/_sentinels/receipts.last_success -
```

If timestamps are recent:

* upload pipelines are healthy
* S3 contents are current

If stale or missing:

* investigate upload jobs before restoring

---

### 2. Identify latest database backup

List available DB backups:

```bash
aws s3 ls s3://ardua-books-backups/postgres/last/
```

Choose the appropriate **timestamped** `.dump` file.

> Do **not** rely on `*-latest.dump` (local symlinks are not authoritative).

---

## Part B — Restore PostgreSQL Database

### 1. Download the backup

```bash
aws s3 cp \
  s3://ardua-books-backups/postgres/last/ardua_books-YYYYMMDD-HHMMSS.dump \
  ./ardua_books.restore.dump
```

Verify size:

```bash
ls -lh ardua_books.restore.dump
```

---

### 2. Restore into target database

```bash
docker run --rm -it \
  -v "$PWD:/restore" \
  postgres:15 \
  pg_restore \
    --clean \
    --if-exists \
    --no-owner \
    --no-acl \
    --dbname=postgresql://USER:PASSWORD@HOST:5432/ardua_books \
    /restore/ardua_books.restore.dump
```

Replace:

* `USER`
* `PASSWORD`
* `HOST`

---

`--no-acl` skips the dump's GRANT statements. They reference the
`books_readonly` role, and roles are cluster-level objects that `pg_dump`
does not include, so restoring them into a fresh cluster fails. The grants
are re-created in the next step.

### 3. Re-apply the read-only role

From `/opt/ardua_books` on books (or wherever `readonly_role.sql` was synced):

```bash
docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < readonly_role.sql
```

On a fresh cluster this creates the role **without a password**, so it can't
log in until you set one. Then, from homer:

```bash
python3 ardua_books/deploy/set_readonly_password.py
```

### 4. Post-restore validation

* Confirm tables exist
* Start application container
* Verify login and basic navigation

---

## Part C — Restore Receipts / Media Files

### 1. Restore receipts from S3

```bash
rclone sync \
  s3:ardua-books-backups/receipts \
  /restore/receipts \
  --checksum --progress
```

This recreates the directory tree exactly.

---

### 2. Reattach to application (if restoring production)

```bash
rsync -a /restore/receipts/ /opt/ardua_books/media/receipts/
```

Restart the web container if needed.

---

## Restore Confidence Checklist

* DB restored from timestamped dump
* Receipts restored via `rclone sync`
* Upload sentinels were recent
* Application behaves normally

If all are true, the restore is complete.
