#!/bin/bash
#
# Migrate data from local SQLite database to production PostgreSQL (Docker)
#
# Usage:
#   ./scripts/migrate_to_production.sh [--metadata-only]
#
# This script:
#   1. Exports data from local SQLite database
#   2. Copies export to the Docker container
#   3. Imports data into PostgreSQL
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
EXPORT_DIR="$PROJECT_DIR/data_export"
CONTAINER_NAME="ardua-books-web"  # Adjust if your container name differs

# Parse arguments
METADATA_ONLY=""
if [[ "$1" == "--metadata-only" ]]; then
    METADATA_ONLY="--metadata-only"
    echo "Mode: Metadata only"
else
    echo "Mode: Full migration (metadata + transactions)"
fi

echo ""
echo "========================================"
echo "  Ardua Books Data Migration"
echo "========================================"
echo ""

# Step 1: Export from local SQLite
echo "Step 1: Exporting data from local SQLite..."
echo "----------------------------------------"

cd "$PROJECT_DIR/ardua_books"

# Activate virtual environment if it exists
if [[ -f "../venv/bin/activate" ]]; then
    source ../venv/bin/activate
fi

# Clean previous export
rm -rf "$EXPORT_DIR"
mkdir -p "$EXPORT_DIR"

python manage.py migrate_data export $METADATA_ONLY --output "$EXPORT_DIR"

echo ""
echo "Step 2: Checking Docker container..."
echo "----------------------------------------"

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    # Try to find a container with 'web' in the name
    CONTAINER_NAME=$(docker ps --format '{{.Names}}' | grep -E 'web|ardua' | head -1)
    if [[ -z "$CONTAINER_NAME" ]]; then
        echo "ERROR: No running container found. Please start your Docker containers first:"
        echo "  docker compose up -d"
        exit 1
    fi
fi

echo "Using container: $CONTAINER_NAME"

echo ""
echo "Step 3: Copying export to container..."
echo "----------------------------------------"

# Copy export directory to container
docker cp "$EXPORT_DIR" "$CONTAINER_NAME:/app/data_export"
echo "Copied to /app/data_export in container"

echo ""
echo "Step 4: Importing data into PostgreSQL..."
echo "----------------------------------------"

# Run import command in container
docker exec -it "$CONTAINER_NAME" python manage.py migrate_data import $METADATA_ONLY --input /app/data_export

echo ""
echo "Step 5: Cleanup..."
echo "----------------------------------------"

# Clean up export in container
docker exec "$CONTAINER_NAME" rm -rf /app/data_export
echo "Removed temporary files from container"

# Optionally clean up local export
read -p "Remove local export directory? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    rm -rf "$EXPORT_DIR"
    echo "Local export removed"
else
    echo "Local export kept at: $EXPORT_DIR"
fi

echo ""
echo "========================================"
echo "  Migration Complete!"
echo "========================================"
echo ""
