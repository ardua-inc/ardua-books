# Backup Architecture — Ardua Books

This document is the **authoritative description** of the Ardua Books backup system.
It is written so the design does **not** need to be re-reasoned later.

---

## Design Summary (TL;DR)

* Backups are **created locally**
* Local backups are **authoritative**
* Amazon S3 is a **mirror**, not the primary store
* Uploads use `rclone sync` (rsync-with-delete semantics)
* **Health sentinels are never written into synced prefixes**

---

## High-Level Architecture

```
PostgreSQL
   │
   ▼
postgres-backup-local (daily, internal cron)
   │
   ▼
/backups (Docker volume: pg_backups)
   │
   ▼
rclone sync (host cron, hourly)
   │
   ▼
Amazon S3
   ├── postgres/
   ├── receipts/
   └── _sentinels/
```

---

## Components

### 1. Database Backup (`db-backup`)

* Image: `prodrigestivill/postgres-backup-local`
* Runs **internally on a schedule** (`SCHEDULE`, `TZ`)
* Writes to `/backups` (named Docker volume)

**Local structure:**

```
/backups/
  daily/
  weekly/
  monthly/
  last/
```

* Timestamped `.dump` files are authoritative
* `*-latest.dump` are **symlinks for local convenience only**

---

### 2. Database Upload (`db-backup-upload`)

* Image: `rclone/rclone`
* One-shot container
* Triggered by **host cron**
* Command pattern:

```
rclone sync /backups s3://ardua-books-backups/postgres
```

**Important behavior:**

* Equivalent to `rsync --delete`
* Files removed locally are removed from S3
* Symlinks are skipped (by design)

---

### 3. Receipts Backup (`receipts-backup`)

* One-shot `rclone sync`
* Source: `/data/receipts`
* Destination: `s3://ardua-books-backups/receipts`
* Same semantics as DB upload

---

## Scheduling Model

### Database backup

* Runs automatically inside container
* Example:

```
TZ=America/Los_Angeles
SCHEDULE="0 0 * * *"   # daily at midnight local time
```

### Upload jobs

* Triggered by host cron
* Recommended cadence: **hourly**

This avoids race conditions and does not rely on knowing exact backup timing.

---

## Health Sentinels (Critical Rule)

### Rule (do not violate)

> **Never write sentinel files into an S3 prefix managed by `rclone sync`.**

`rclone sync` will delete any object not present in the source.

---

### Sentinel location (correct)

All sentinels live in a **separate prefix**:

```
s3://ardua-books-backups/_sentinels/
```

Examples:

```
_sentinels/postgres.last_success
_sentinels/receipts.last_success
```

---

### Meaning of sentinels

A sentinel file means:

> “The upload pipeline completed successfully at this time.”

It does **not** assert that a *new* backup was created in that run.

---

## Cron Jobs (Canonical)

### Receipts upload + sentinel

```
15 3 * * * root \
  cd /opt/ardua_books && \
  /usr/bin/docker compose run --rm receipts-backup > /dev/null 2>&1 && \
  date -u +"%Y-%m-%dT%H:%M:%SZ OK" | \
  /snap/bin/aws s3 cp - s3://ardua-books-backups/_sentinels/receipts.last_success
```

---

### Database upload + sentinel

```
10 * * * * root \
  cd /opt/ardua_books && \
  /usr/bin/docker compose run --rm db-backup-upload > /dev/null 2>&1 && \
  date -u +"%Y-%m-%dT%H:%M:%SZ OK" | \
  /snap/bin/aws s3 cp - s3://ardua-books-backups/_sentinels/postgres.last_success
```

---

## How to Assess Health (Correct Interpretation)

| Signal                             | Meaning                   |
| ---------------------------------- | ------------------------- |
| `/backups/last/*.dump` timestamp   | DB backup freshness       |
| S3 contains latest dump            | Upload succeeded          |
| `_sentinels/*.last_success` recent | Upload pipeline healthy   |
| Sentinel stale                     | Upload broken             |
| Backup fresh, sentinel stale       | Upload failure            |
| Sentinel fresh, backup stale       | Backup not producing data |

Each signal answers **one specific question**. None are overloaded.

---

## Restore Principles (Summary)

* Always restore from **timestamped `.dump` files**
* Do not rely on `*-latest.dump`
* S3 mirrors local retention exactly
* Receipts restore via `rclone sync` in reverse

(See restore runbook for step-by-step instructions.)

---

## In One Sentence

> Backups are created locally, mirrored to S3 with strict sync semantics, and monitored using sentinels stored **outside** synced paths to avoid false deletion.

---

## Things Not to Change Casually

* Replacing `rclone sync` with `copy`
* Writing metadata into synced prefixes
* Coupling upload timing to backup timing
* Removing the `/backups` volume
* Using Docker restart policies as schedulers

---

**If this document still makes sense in two years, the system is healthy.**
