# Backup Architecture — Ardua Books

## Overview

This system implements **container-native, decoupled backups** for Ardua Books, covering:

* **PostgreSQL database**
* **Receipt/media files**

Backups are created locally, retained with rotation, and **mirrored to Amazon S3**.
Creation and upload are intentionally **separate steps** to avoid timing coupling and hidden failure modes.

The design prioritizes:

* determinism
* inspectability
* idempotence
* easy restore drills

---

## High-Level Architecture

```
PostgreSQL
   │
   ▼
postgres-backup-local (daily, internal cron)
   │
   ▼
/backups (Docker named volume: pg_backups)
   │
   ▼
rclone sync (cron, host)
   │
   ▼
Amazon S3
```

Receipts follow the same pattern (media volume → rclone → S3).

---

## Components

### 1. Database Backup Container (`db-backup`)

* Image: `prodrigestivill/postgres-backup-local`
* Runs its **own internal cron**
* Schedule defined by `SCHEDULE` and `TZ`
* Writes backups to `/backups` (mounted volume)

**Retention policy (local & authoritative):**

* Daily backups
* Weekly backups
* Monthly backups
* `last/` directory with timestamped dump
* `*-latest.dump` symlinks (local convenience only)

> `/backups` is the source of truth for database backups.

---

### 2. Database Upload Job (`db-backup-upload`)

* Image: `rclone/rclone`
* One-shot container
* Invoked via **host crontab**
* Command: `rclone sync /backups s3://…/postgres`

**Important behavior:**

* Equivalent to `rsync --delete`
* S3 mirrors local retention exactly
* Symlinks (`*-latest.dump`) are skipped by default
* Only real dump files are stored in S3

---

### 3. Receipts Backup (`receipts-backup`)

* Image: `rclone/rclone`
* One-shot container
* Syncs `/data/receipts` → `s3://…/receipts`
* Uses same rclone configuration and credentials

---

## Scheduling Model

### Database backup

* Runs automatically inside container
* Example:

  ```
  TZ=America/Los_Angeles
  SCHEDULE="0 0 * * *"   # daily at midnight local time
  ```

### Database upload

* Triggered by host cron
* Recommended: **hourly**

  ```
  15 * * * * docker compose run --rm db-backup-upload
  ```

This avoids race conditions and removes the need to know *exactly* when the DB backup ran.

---

## Sentinel Files (Health Indicators)

### Upload sentinel (S3)

After a successful upload, cron writes:

```
s3://<bucket>/postgres/.last_success
```

Contents:

```
2025-12-18T01:15:04Z OK
```

**Meaning:**

> “As of this timestamp, the upload pipeline ran successfully.”

This does **not** assert freshness of the database backup itself.

---

## How to Assess System Health

| Check                             | Meaning                          |
| --------------------------------- | -------------------------------- |
| `/backups/last/*.dump` timestamp  | When DB backup actually ran      |
| S3 contains latest dump           | Upload succeeded                 |
| `.last_success` is recent         | Upload pipeline healthy          |
| `.last_success` stale             | Upload failing                   |
| Backup file stale, sentinel fresh | DB backup not producing new data |
| Backup file fresh, sentinel stale | Upload failing                   |

No single indicator lies; each answers a specific question.

---

## Restore Strategy (Summary)

### Database

1. Download desired `.dump` from S3
2. Restore with `pg_restore` into target database
3. Do **not** rely on `*-latest.dump` (local-only symlinks)

### Receipts

1. `rclone sync s3://…/receipts /restore/path`
2. Files are already in final layout

---

## Design Rationale

* **Decoupled steps** avoid fragile timing assumptions
* **Local retention is authoritative**
* **S3 is a mirror, not the primary store**
* **One-shot containers** avoid loops and hidden schedulers
* **Sentinels report liveness, not inferred success**

This design intentionally favors clarity over cleverness.

---

## What Not to Change Without Re-Thinking

* Replacing `rclone sync` with `copy`
* Relying on Docker restart policies as schedulers
* Assuming S3 contents alone imply backup freshness
* Removing the `/backups` volume

---

## In One Sentence

> Backups are created locally, retained deterministically, mirrored to S3, and monitored with explicit signals — no magic, no timing guesses, no hidden state.

