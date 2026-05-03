#!/usr/bin/env bash
# Query the current robot pose from the backend commissioning API.
# Backend and localization must be running for a meaningful result.
#
# Usage:
#   ./scripts/commissioning/check_pose.sh
#   DRY_RUN=1 ./scripts/commissioning/check_pose.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/commissioning/_common.sh
source "$SCRIPT_DIR/_common.sh"

require_command curl
require_token

info "Querying current pose from $API_BASE_URL"

response="$(curl_json GET /commissioning/pose)" \
    || fail "Current pose unavailable. Check backend, localization, /pose, and position_source."

printf '%s\n' "$response"
