#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Titan-Karu Overnight Harness Bootstrap Script
# ---------------------------------------------------------------------------
# Usage:
#   ./run_overnight.sh [--manifest PATH] [--dry-run]
#
# This script:
# 1. Resolves the Titan-Karu codebase directory
# 2. Creates the overnight isolation base directory
# 3. Runs the overnight harness with appropriate environment
# 4. Logs output to the artifact directory
#
# For launchd integration, see scripts/titan-overnight.plist
# ---------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEBASE_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
MANIFEST_PATH=""
DRY_RUN=""
MAX_HOURS=""

usage() {
    echo "Usage: $0 [--manifest PATH] [--dry-run] [--max-hours HOURS]"
    echo ""
    echo "Options:"
    echo "  --manifest PATH    Path to manifest YAML (default: CODEBASE/config/overnight_manifest.yaml)"
    echo "  --dry-run           Load manifest and print summary without running"
    echo "  --max-hours HOURS   Override runtime.max_hours from manifest"
    echo "  --help              Show this help message"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --manifest)
            MANIFEST_PATH="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN="1"
            shift
            ;;
        --max-hours)
            MAX_HOURS="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Default manifest path
if [[ -z "$MANIFEST_PATH" ]]; then
    MANIFEST_PATH="$CODEBASE_DIR/config/overnight_manifest.yaml"
fi

# Resolve full path
MANIFEST_PATH="$(cd "$(dirname "$MANIFEST_PATH")" && pwd)/$(basename "$MANIFEST_PATH")"

echo "[titan-overnight] Codebase: $CODEBASE_DIR"
echo "[titan-overnight] Manifest: $MANIFEST_PATH"
echo "[titan-overnight] Run at:   $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# Detect Python: prefer .venv in codebase if it exists and has yaml installed
if [[ -x "$CODEBASE_DIR/.venv/bin/python3" ]]; then
    PYTHON="$CODEBASE_DIR/.venv/bin/python3"
    # Quick check that yaml is importable
    if "$PYTHON" -c "import yaml" 2>/dev/null; then
        echo "[titan-overnight] Using virtual environment: $CODEBASE_DIR/.venv"
    else
        echo "[titan-overnight] .venv exists but missing dependencies; falling back to python3"
        PYTHON="python3"
    fi
else
    PYTHON="python3"
fi

# Build command
CMD=("$PYTHON" "-m" "entrypoints.overnight.runner" "--manifest" "$MANIFEST_PATH")

if [[ -n "$DRY_RUN" ]]; then
    CMD+=("--dry-run")
fi

if [[ -n "$MAX_HOURS" ]]; then
    CMD+=("--max-hours" "$MAX_HOURS")
fi

# Run the harness
# Note: we deliberately do NOT set TITAN_BASE_DIR here — the runner does it from the manifest
# This ensures the bootstrap script is stateless and re-runnable

cd "$CODEBASE_DIR"

if [[ -n "${TITAN_OVERNIGHT_LABEL:-}" ]]; then
    export TITAN_OVERNIGHT_LABEL
fi

exec "${CMD[@]}"
