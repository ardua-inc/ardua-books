#!/bin/bash
#
# Import data into local database (or run in Docker for production)
#
# Usage:
#   ./scripts/import_data.sh [--metadata-only] [input_dir]
#
# For production (inside Docker):
#   docker exec -it <container> python manage.py migrate_data import --input /path/to/data
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Parse arguments
METADATA_ONLY=""
INPUT_DIR="$PROJECT_DIR/data_export"

for arg in "$@"; do
    if [[ "$arg" == "--metadata-only" ]]; then
        METADATA_ONLY="--metadata-only"
    elif [[ "$arg" != -* ]]; then
        INPUT_DIR="$arg"
    fi
done

if [[ ! -d "$INPUT_DIR" ]]; then
    echo "ERROR: Input directory does not exist: $INPUT_DIR"
    exit 1
fi

echo "Importing data..."
echo "  Mode: ${METADATA_ONLY:-full}"
echo "  Input: $INPUT_DIR"
echo ""

cd "$PROJECT_DIR/ardua_books"

# Activate virtual environment if it exists
if [[ -f "../venv/bin/activate" ]]; then
    source ../venv/bin/activate
fi

python manage.py migrate_data import $METADATA_ONLY --input "$INPUT_DIR"

echo ""
echo "Import complete!"
