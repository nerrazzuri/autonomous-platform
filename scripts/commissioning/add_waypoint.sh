#!/usr/bin/env bash
# Add the robot's current pose as a waypoint on a commissioning route.
# Move the robot manually to the waypoint position before running this.
#
# Usage:
#   ./scripts/commissioning/add_waypoint.sh ROUTE_ID
#   ./scripts/commissioning/add_waypoint.sh ROUTE_ID WAYPOINT_ID
#   ./scripts/commissioning/add_waypoint.sh ROUTE_ID WAYPOINT_ID --hold HOLD_REASON
#   ./scripts/commissioning/add_waypoint.sh ROUTE_ID WAYPOINT_ID --no-hold
#
# Valid hold reasons: awaiting_load awaiting_unload manual_check
# DRY_RUN=1 prints the curl command without executing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/commissioning/_common.sh
source "$SCRIPT_DIR/_common.sh"

VALID_HOLD_REASONS=(awaiting_load awaiting_unload manual_check)

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
if [[ $# -lt 1 ]] || [[ -z "${1:-}" ]]; then
    printf 'Usage: %s ROUTE_ID [WAYPOINT_ID] [--hold HOLD_REASON | --no-hold]\n' "$(basename "$0")" >&2
    printf 'Valid hold reasons: %s\n' "${VALID_HOLD_REASONS[*]}" >&2
    exit 1
fi

ROUTE_ID="$1"
shift

WAYPOINT_ID=""
HOLD=false
HOLD_REASON="null"

# Second positional arg is waypoint_id if it doesn't start with --
if [[ $# -gt 0 ]] && [[ "${1:-}" != --* ]]; then
    WAYPOINT_ID="$1"
    shift
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --hold)
            shift
            if [[ $# -eq 0 ]] || [[ -z "${1:-}" ]]; then
                fail "--hold requires a hold reason argument (awaiting_load, awaiting_unload, manual_check)"
            fi
            HOLD=true
            HOLD_REASON="$1"
            shift
            ;;
        --no-hold)
            HOLD=false
            HOLD_REASON="null"
            shift
            ;;
        *)
            fail "Unknown argument: $1"
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
if [[ -z "$ROUTE_ID" ]]; then
    fail "route_id must not be empty."
fi

if [[ -z "$WAYPOINT_ID" ]]; then
    warn "No waypoint_id provided — backend will auto-generate an ID."
fi

if [[ "$HOLD" == "true" ]]; then
    if [[ -z "$HOLD_REASON" ]] || [[ "$HOLD_REASON" == "null" ]]; then
        fail "hold_reason must not be empty when --hold is specified."
    fi
    valid_reason=0
    for r in "${VALID_HOLD_REASONS[@]}"; do
        [[ "$HOLD_REASON" == "$r" ]] && valid_reason=1 && break
    done
    if [[ "$valid_reason" -eq 0 ]]; then
        fail "Invalid hold reason '$HOLD_REASON'. Valid: ${VALID_HOLD_REASONS[*]}"
    fi
fi

require_command curl
require_token

# ---------------------------------------------------------------------------
# Build JSON body
# ---------------------------------------------------------------------------
if [[ -n "$WAYPOINT_ID" ]]; then
    WP_JSON="\"$WAYPOINT_ID\""
else
    WP_JSON="null"
fi

if [[ "$HOLD_REASON" == "null" ]]; then
    HR_JSON="null"
else
    HR_JSON="\"$HOLD_REASON\""
fi

BODY="{\"waypoint_id\":${WP_JSON},\"hold\":${HOLD},\"hold_reason\":${HR_JSON}}"

info "Adding waypoint to route: $ROUTE_ID"
[[ -n "$WAYPOINT_ID" ]] && info "  waypoint_id: $WAYPOINT_ID"
info "  hold: $HOLD  hold_reason: $HOLD_REASON"

response="$(curl_json POST "/commissioning/routes/${ROUTE_ID}/waypoints/add-current" "$BODY")"

printf '%s\n' "$response"

[[ "$DRY_RUN" == "1" ]] || info "Waypoint added to route $ROUTE_ID."
