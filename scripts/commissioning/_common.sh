#!/usr/bin/env bash
# Sourced by commissioning scripts. Do not execute directly.
# Caller scripts must set: set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

DRY_RUN="${DRY_RUN:-0}"
API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:8080}"
DATA_DIR="${DATA_DIR:-$PROJECT_ROOT/data}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/data/backups}"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
info() { printf 'INFO  %s\n' "$1"; }
warn() { printf 'WARN  %s\n' "$1" >&2; }
fail() { printf 'ERROR %s\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------
require_command() {
    local cmd="$1"
    command -v "$cmd" >/dev/null 2>&1 || fail "Required command not found: $cmd"
}

require_token() {
    if [[ -z "${SUPERVISOR_TOKEN:-}" ]]; then
        if [[ "$DRY_RUN" != "1" ]]; then
            fail "SUPERVISOR_TOKEN is required. Export it first."
        fi
    fi
}

# ---------------------------------------------------------------------------
# Auth and API helpers
# ---------------------------------------------------------------------------
auth_header() {
    printf 'Authorization: Bearer %s' "${SUPERVISOR_TOKEN:-}"
}

api_url() {
    local path="$1"
    printf '%s%s' "$API_BASE_URL" "$path"
}

# curl_json METHOD PATH [BODY]
# In DRY_RUN mode: prints the command without executing.
# Fails on non-2xx HTTP status or curl connection error.
curl_json() {
    local method="$1"
    local path="$2"
    local data="${3:-}"
    local url
    url="$(api_url "$path")"

    if [[ "$DRY_RUN" == "1" ]]; then
        local data_flag=""
        [[ -n "$data" ]] && data_flag=" -d '${data}'"
        printf '[DRY_RUN] curl -sS -X %s \\\n' "$method"
        printf '  -H "Authorization: Bearer <SUPERVISOR_TOKEN>" \\\n'
        printf '  -H "Content-Type: application/json"%s \\\n' "$data_flag"
        printf '  "%s"\n' "$url"
        return 0
    fi

    local tmpfile
    tmpfile="$(mktemp)"
    local extra_args=()
    [[ -n "$data" ]] && extra_args+=(-d "$data")

    local http_code
    http_code="$(curl -sS \
        -w "%{http_code}" \
        -o "$tmpfile" \
        -X "$method" \
        -H "Content-Type: application/json" \
        -H "$(auth_header)" \
        "${extra_args[@]}" \
        "$url")" \
        || { rm -f "$tmpfile"; fail "curl failed — check backend at $url"; }

    local body
    body="$(cat "$tmpfile")"
    rm -f "$tmpfile"

    printf '%s\n' "$body"

    if [[ "$http_code" -lt 200 ]] || [[ "$http_code" -ge 300 ]]; then
        fail "API returned HTTP $http_code: $body"
    fi
}

# dry_run_or_exec CMD [ARGS...]
# Prints the command in DRY_RUN mode; executes otherwise.
dry_run_or_exec() {
    if [[ "$DRY_RUN" == "1" ]]; then
        printf '[DRY_RUN] %s\n' "$*"
    else
        "$@"
    fi
}
