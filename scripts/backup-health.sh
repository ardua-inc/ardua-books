#!/usr/bin/env bash
set -euo pipefail

BUCKET="ardua-books-backups"

echo "=== Backup Health Check ($(date -u)) ==="

echo
echo "Database backup (local):"
ls -lh /var/lib/docker/volumes/*pg_backups*/_data/last 2>/dev/null || \
  echo "  ERROR: No local DB backups found"

echo
echo "Database backup (S3):"
aws s3 ls "s3://$BUCKET/postgres/last/" || \
  echo "  ERROR: Cannot list DB backups in S3"

echo
echo "DB upload sentinel:"
aws s3 cp "s3://$BUCKET/postgres/.last_success" - 2>/dev/null || \
  echo "  ERROR: DB upload sentinel missing or unreadable"

echo
echo "Receipts upload sentinel:"
aws s3 cp "s3://$BUCKET/receipts/.last_success" - 2>/dev/null || \
  echo "  ERROR: Receipts upload sentinel missing or unreadable"

echo
echo "=== End Health Check ==="

