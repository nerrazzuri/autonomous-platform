#!/usr/bin/env bash
# Mark the robot's current pose as a commissioning station.
# Move the robot manually to the station before running this.
#
# Usage:
#   ./scripts/commissioning/mark_station.sh STATION_ID
#   DRY_RUN=1 ./scripts/commissioning/mark_station.sh LINE_A
#
# Valid station IDs: LINE_A LINE_B LINE_C QA DOCK
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/commissioning/_common.sh
source "$SCRIPT_DIR/_common.sh"

VALID_STATIONS=(LINE_A LINE_B LINE_C QA DOCK)

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------
if [[ $# -lt 1 ]] || [[ -z "${1:-}" ]]; then
    printf 'Usage: %s STATION_ID\n' "$(basename "$0")" >&2
    printf 'Valid station IDs: %s\n' "${VALID_STATIONS[*]}" >&2
    exit 1
fi

STATION_ID="$1"

# Allowlist check
valid=0
for s in "${VALID_STATIONS[@]}"; do
    [[ "$STATION_ID" == "$s" ]] && valid=1 && break
done
if [[ "$valid" -eq 0 ]]; then
    warn "Station ID '$STATION_ID' is not in the standard allowlist: ${VALID_STATIONS[*]}"
    warn "Proceeding anyway — verify the station ID matches your config."
fi

require_command curl
require_token

info "Marking current pose as station: $STATION_ID"

response="$(curl_json POST "/commissioning/stations/${STATION_ID}/mark-current" '{}')"

printf '%s\n' "$response"

[[ "$DRY_RUN" == "1" ]] || info "Station $STATION_ID marked successfully."
