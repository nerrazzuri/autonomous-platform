#!/usr/bin/env bash
# Clear the placeholder flag on a route, marking it ready for navigation.
# The route must have at least one waypoint; the API will reject otherwise.
#
# Usage:
#   ./scripts/commissioning/set_route_ready.sh ROUTE_ID
#   DRY_RUN=1 ./scripts/commissioning/set_route_ready.sh LINE_A_TO_QA
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/commissioning/_common.sh
source "$SCRIPT_DIR/_common.sh"

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------
if [[ $# -lt 1 ]] || [[ -z "${1:-}" ]]; then
    printf 'Usage: %s ROUTE_ID\n' "$(basename "$0")" >&2
    exit 1
fi

ROUTE_ID="$1"

require_command curl
require_token

info "Marking route '$ROUTE_ID' as ready (placeholder=false)"

# curl_json fails on non-2xx. Capture stderr to check for the no-waypoints case.
tmpfile="$(mktemp)"
response="$(curl_json POST "/commissioning/routes/${ROUTE_ID}/placeholder" '{"placeholder":false}' 2>"$tmpfile")" || {
    err="$(cat "$tmpfile")"
    rm -f "$tmpfile"
    if printf '%s' "$err" | grep -qi "waypoint"; then
        fail "Route cannot be marked ready without at least one waypoint."
    fi
    fail "$err"
}
rm -f "$tmpfile"

printf '%s\n' "$response"

[[ "$DRY_RUN" == "1" ]] || info "Route '$ROUTE_ID' is now marked ready."
