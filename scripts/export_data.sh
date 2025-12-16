#!/bin/bash
#
# Export data from local database
#
# Usage:
#   ./scripts/export_data.sh [--metadata-only] [output_dir]
#
# Examples:
#   ./scripts/export_data.sh                           # Full export to ./data_export
#   ./scripts/export_data.sh --metadata-only           # Metadata only to ./data_export
#   ./scripts/export_data.sh /path/to/output           # Full export to custom dir
#   ./scripts/export_data.sh --metadata-only /path     # Metadata only to custom dir
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Parse arguments
METADATA_ONLY=""
OUTPUT_DIR="$PROJECT_DIR/data_export"

for arg in "$@"; do
    if [[ "$arg" == "--metadata-only" ]]; then
        METADATA_ONLY="--metadata-only"
    elif [[ "$arg" != -* ]]; then
        OUTPUT_DIR="$arg"
    fi
done

echo "Exporting data..."
echo "  Mode: ${METADATA_ONLY:-full}"
echo "  Output: $OUTPUT_DIR"
echo ""

cd "$PROJECT_DIR/ardua_books"

# Activate virtual environment if it exists
if [[ -f "../venv/bin/activate" ]]; then
    source ../venv/bin/activate
fi

python manage.py migrate_data export $METADATA_ONLY --output "$OUTPUT_DIR"

echo ""
echo "Export complete: $OUTPUT_DIR"
