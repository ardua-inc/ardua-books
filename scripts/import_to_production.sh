#!/bin/bash
#
# This script:
#   1. Copies export to the Docker container
#   2. Imports data into PostgreSQL

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
EXPORT_DIR="$PROJECT_DIR/data_export"
CONTAINER_NAME="ardua-books-web"  # Adjust if your container name differs

echo ""
echo "========================================"
echo "  Ardua Books Data Migration"
echo "========================================"
echo ""

echo ""
echo "Step 1: Checking Docker container..."
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
echo "Step 2: Copying export to container..."
echo "----------------------------------------"

# Copy export directory to container
docker cp "$EXPORT_DIR" "$CONTAINER_NAME:/app/data_export"
echo "Copied to /app/data_export in container"

echo ""
echo "Step 3: Importing data into PostgreSQL..."
echo "----------------------------------------"

# Run import command in container
docker exec -it "$CONTAINER_NAME" python manage.py migrate_data import $METADATA_ONLY --input /app/data_export

echo ""
echo "Step 4: Cleanup..."
echo "----------------------------------------"

# Clean up export in container
docker exec "$CONTAINER_NAME" rm -rf /app/data_export
echo "Removed temporary files from container"

echo ""
echo "========================================"
echo "  Migration Complete!"
echo "========================================"
echo ""
