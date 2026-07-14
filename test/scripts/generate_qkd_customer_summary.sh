#!/bin/sh

set -eu

LOG_DIR="${1:-/var/tmp}"
OUT_DIR="${2:-$LOG_DIR}"
TITLE="${3:-Customer QKD Health Summary}"

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
PY_TOOL="$REPO_ROOT/lib/qkd/log_summary.py"

if [ ! -f "$PY_TOOL" ]; then
    echo "[ERROR] Missing summary tool: $PY_TOOL" >&2
    exit 1
fi

if [ ! -d "$LOG_DIR" ]; then
    echo "[ERROR] Log directory not found: $LOG_DIR" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"

LOG_FILES="$(find "$LOG_DIR" -maxdepth 1 -type f -name 'qkd_debug*.log' | sort)"

if [ -z "$LOG_FILES" ]; then
    echo "[ERROR] No qkd_debug*.log files found in: $LOG_DIR" >&2
    exit 1
fi

set --
for file in $LOG_FILES; do
    set -- "$@" "$file"
done

TS="$(date '+%Y%m%d_%H%M%S')"
OUT_FILE="$OUT_DIR/qkd_customer_summary_${TS}.log"

python3 "$PY_TOOL" --logs "$@" --output "$OUT_FILE" --title "$TITLE"

echo "[OK] Customer summary: $OUT_FILE"
echo "[INFO] Source logs:"
for file in "$@"; do
    echo "  - $file"
done
