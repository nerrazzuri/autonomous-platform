#!/usr/bin/env bash
# Create the project Python 3.10 venv and install requirements.txt.
# Safe to re-run: skips creation if .venv already exists unless FORCE_RECREATE_VENV=1.
#
# Usage:
#   bash scripts/setup/setup_python_env.sh
#   DRY_RUN=1 bash scripts/setup/setup_python_env.sh
#   FORCE_RECREATE_VENV=1 bash scripts/setup/setup_python_env.sh
set -euo pipefail

DRY_RUN="${DRY_RUN:-0}"
FORCE_RECREATE_VENV="${FORCE_RECREATE_VENV:-0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
REQUIREMENTS="$ROOT_DIR/requirements.txt"

run() {
    if [[ "$DRY_RUN" == "1" ]]; then
        printf '[DRY_RUN] %s\n' "$*"
    else
        "$@"
    fi
}

section() { printf '\n==> %s\n' "$1"; }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
section "Preflight"

if ! command -v python3.10 >/dev/null 2>&1; then
    printf 'ERROR: python3.10 not found.\n' >&2
    printf '       Run install_ubuntu_workstation_deps.sh first.\n' >&2
    exit 1
fi

if ! python3.10 -m venv --help >/dev/null 2>&1; then
    printf 'ERROR: python3.10-venv module not available.\n' >&2
    printf '       Run: sudo apt-get install python3.10-venv\n' >&2
    exit 1
fi

if [[ ! -f "$REQUIREMENTS" ]]; then
    printf 'ERROR: requirements.txt not found at %s\n' "$REQUIREMENTS" >&2
    exit 1
fi

printf 'Project root : %s\n' "$ROOT_DIR"
printf 'Venv path    : %s\n' "$VENV_DIR"
printf 'Requirements : %s\n' "$REQUIREMENTS"

# ---------------------------------------------------------------------------
# Create venv
# ---------------------------------------------------------------------------
section "Virtual environment"

if [[ -d "$VENV_DIR" ]] && [[ "$FORCE_RECREATE_VENV" == "1" ]]; then
    printf 'FORCE_RECREATE_VENV=1 — removing existing .venv\n'
    run rm -rf "$VENV_DIR"
fi

if [[ -d "$VENV_DIR" ]]; then
    printf '.venv already exists — skipping creation (set FORCE_RECREATE_VENV=1 to recreate).\n'
else
    printf 'Creating .venv with python3.10...\n'
    run python3.10 -m venv "$VENV_DIR"
    printf 'Created: %s\n' "$VENV_DIR"
fi

# ---------------------------------------------------------------------------
# Upgrade pip
# ---------------------------------------------------------------------------
section "pip upgrade"
run "$VENV_DIR/bin/python" -m pip install --upgrade pip

# ---------------------------------------------------------------------------
# Install requirements
# ---------------------------------------------------------------------------
section "Install requirements.txt"
run "$VENV_DIR/bin/pip" install -r "$REQUIREMENTS"

# ---------------------------------------------------------------------------
# Verify key packages
# ---------------------------------------------------------------------------
section "Verification"
KEY_PACKAGES=(pydantic fastapi uvicorn anthropic pyserial websockets)
all_ok=1
for pkg in "${KEY_PACKAGES[@]}"; do
    if [[ "$DRY_RUN" == "1" ]]; then
        printf '[DRY_RUN] would check: %s\n' "$pkg"
    else
        if "$VENV_DIR/bin/python" -c "import $(printf '%s' "$pkg" | tr '-' '_')" 2>/dev/null; then
            printf 'PASS  %s importable\n' "$pkg"
        else
            printf 'FAIL  %s not importable after install\n' "$pkg"
            all_ok=0
        fi
    fi
done

if [[ "$DRY_RUN" != "1" ]] && [[ "$all_ok" -eq 0 ]]; then
    printf 'ERROR: One or more packages failed to import — check pip output above.\n' >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
printf '\n============================\n'
if [[ "$DRY_RUN" == "1" ]]; then
    printf 'DRY RUN complete — no changes made.\n'
else
    printf 'setup_python_env.sh complete.\n'
    printf 'Activate with: source .venv/bin/activate\n'
fi
printf '============================\n'
