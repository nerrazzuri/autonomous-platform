#!/usr/bin/env bash
# Back up current route and station JSON files to a timestamped directory.
# Safe to run any time — does not modify existing files.
#
# Usage:
#   ./scripts/commissioning/backup_routes.sh
#   ./scripts/commissioning/backup_routes.sh before-line-a-qa-test
#   DRY_RUN=1 ./scripts/commissioning/backup_routes.sh label
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/commissioning/_common.sh
source "$SCRIPT_DIR/_common.sh"

LABEL="${1:-}"

# ---------------------------------------------------------------------------
# Determine backup destination
# ---------------------------------------------------------------------------
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
if [[ -n "$LABEL" ]]; then
    DEST_NAME="routes_${TIMESTAMP}_${LABEL}"
else
    DEST_NAME="routes_${TIMESTAMP}"
fi
DEST="$BACKUP_DIR/$DEST_NAME"

SOURCE_FILES=(
    "$DATA_DIR/routes.json"
    "$DATA_DIR/stations.json"
    "$DATA_DIR/logistics_routes.json"
)

if [[ "$DRY_RUN" == "1" ]]; then
    printf '[DRY_RUN] backup destination: %s\n' "$DEST"
    for f in "${SOURCE_FILES[@]}"; do
        if [[ -f "$f" ]]; then
            printf '[DRY_RUN] would copy: %s\n' "$f"
        else
            printf '[DRY_RUN] skip (not found): %s\n' "$f"
        fi
    done
    printf '[DRY_RUN] would write: %s/MANIFEST.txt\n' "$DEST"
    exit 0
fi

# ---------------------------------------------------------------------------
# Create backup directory
# ---------------------------------------------------------------------------
mkdir -p "$DEST"
info "Backup destination: $DEST"

# ---------------------------------------------------------------------------
# Copy files
# ---------------------------------------------------------------------------
copied=()
for f in "${SOURCE_FILES[@]}"; do
    if [[ -f "$f" ]]; then
        cp "$f" "$DEST/"
        copied+=("$(basename "$f")")
        info "  copied: $(basename "$f")"
    else
        warn "  not found (skipped): $f"
    fi
done

if [[ "${#copied[@]}" -eq 0 ]]; then
    rmdir "$DEST"
    fail "No route files found to back up. Check DATA_DIR=$DATA_DIR"
fi

# ---------------------------------------------------------------------------
# Write MANIFEST.txt
# ---------------------------------------------------------------------------
GIT_HASH="$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || printf 'unavailable')"
{
    printf 'timestamp: %s\n' "$TIMESTAMP"
    printf 'source: %s\n' "$PROJECT_ROOT"
    printf 'git_commit: %s\n' "$GIT_HASH"
    printf 'files:\n'
    for f in "${copied[@]}"; do
        printf '  - %s\n' "$f"
    done
} > "$DEST/MANIFEST.txt"

info "Backup complete: $DEST"
printf '%s\n' "$DEST"
