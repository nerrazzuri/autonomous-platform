#!/usr/bin/env bash
# Restore route and station JSON files from a backup directory.
# Creates a safety backup of current files before overwriting.
#
# Usage:
#   ./scripts/commissioning/restore_routes.sh data/backups/routes_20260503_153000
#   ./scripts/commissioning/restore_routes.sh latest
#   FORCE=1 ./scripts/commissioning/restore_routes.sh latest
#   DRY_RUN=1 ./scripts/commissioning/restore_routes.sh latest
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/commissioning/_common.sh
source "$SCRIPT_DIR/_common.sh"

FORCE="${FORCE:-0}"
RESTORE_FILES=(routes.json stations.json logistics_routes.json)

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------
if [[ $# -lt 1 ]] || [[ -z "${1:-}" ]]; then
    printf 'Usage: %s BACKUP_DIR|latest\n' "$(basename "$0")" >&2
    printf '       FORCE=1 %s latest\n' "$(basename "$0")" >&2
    exit 1
fi

ARG="$1"

# ---------------------------------------------------------------------------
# Resolve backup path
# ---------------------------------------------------------------------------
if [[ "$ARG" == "latest" ]]; then
    BACKUP_PATH="$(find "$BACKUP_DIR" -maxdepth 1 -type d -name 'routes_*' 2>/dev/null \
        | sort -r | head -1 || true)"
    if [[ -z "$BACKUP_PATH" ]]; then
        fail "No backup directories found under $BACKUP_DIR"
    fi
    info "Latest backup: $BACKUP_PATH"
else
    # Accept absolute path or relative to project root
    if [[ "$ARG" = /* ]]; then
        BACKUP_PATH="$ARG"
    else
        BACKUP_PATH="$PROJECT_ROOT/$ARG"
    fi
fi

if [[ ! -d "$BACKUP_PATH" ]]; then
    fail "Backup directory not found: $BACKUP_PATH"
fi

# ---------------------------------------------------------------------------
# Determine which files are present in the backup
# ---------------------------------------------------------------------------
to_restore=()
for f in "${RESTORE_FILES[@]}"; do
    [[ -f "$BACKUP_PATH/$f" ]] && to_restore+=("$f")
done

if [[ "${#to_restore[@]}" -eq 0 ]]; then
    fail "No restorable files found in $BACKUP_PATH"
fi

info "Files to restore: ${to_restore[*]}"
info "Source backup  : $BACKUP_PATH"
info "Destination    : $DATA_DIR"

if [[ "$DRY_RUN" == "1" ]]; then
    printf '[DRY_RUN] would create safety backup under %s\n' "$BACKUP_DIR"
    for f in "${to_restore[@]}"; do
        printf '[DRY_RUN] would restore: %s -> %s\n' "$BACKUP_PATH/$f" "$DATA_DIR/$f"
    done
    exit 0
fi

# ---------------------------------------------------------------------------
# Confirmation (unless FORCE=1)
# ---------------------------------------------------------------------------
if [[ "$FORCE" != "1" ]]; then
    if [[ ! -t 0 ]]; then
        fail "Set FORCE=1 to restore non-interactively."
    fi
    printf '\nThis will overwrite:\n'
    for f in "${to_restore[@]}"; do
        printf '  %s/%s\n' "$DATA_DIR" "$f"
    done
    printf 'A safety backup will be created first.\n'
    printf 'Continue? [y/N] '
    read -r CONFIRM
    if [[ "$CONFIRM" != "y" ]] && [[ "$CONFIRM" != "Y" ]]; then
        info "Restore cancelled."
        exit 0
    fi
fi

# ---------------------------------------------------------------------------
# Safety backup of current files before overwriting
# ---------------------------------------------------------------------------
SAFETY_TS="$(date +%Y%m%d_%H%M%S)"
SAFETY_DIR="$BACKUP_DIR/routes_${SAFETY_TS}_before_restore"
mkdir -p "$SAFETY_DIR"
safety_copied=()
for f in "${to_restore[@]}"; do
    if [[ -f "$DATA_DIR/$f" ]]; then
        cp "$DATA_DIR/$f" "$SAFETY_DIR/"
        safety_copied+=("$f")
    fi
done
GIT_HASH="$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || printf 'unavailable')"
{
    printf 'timestamp: %s\n' "$SAFETY_TS"
    printf 'source: %s\n' "$PROJECT_ROOT"
    printf 'git_commit: %s\n' "$GIT_HASH"
    printf 'note: auto safety backup before restore from %s\n' "$BACKUP_PATH"
    printf 'files:\n'
    for f in "${safety_copied[@]}"; do
        printf '  - %s\n' "$f"
    done
} > "$SAFETY_DIR/MANIFEST.txt"
info "Safety backup created: $SAFETY_DIR"

# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------
for f in "${to_restore[@]}"; do
    cp "$BACKUP_PATH/$f" "$DATA_DIR/$f"
    info "  restored: $f"
done

info "Restore complete from: $BACKUP_PATH"
