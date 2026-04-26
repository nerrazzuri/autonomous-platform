#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8081}"
SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN:-change-me-supervisor}"
ROUTE_ID="${ROUTE_ID:-PATROL_NORTH_LOOP}"

TMP_BODY="$(mktemp)"
TMP_STATUS="$(mktemp)"
trap 'rm -f "$TMP_BODY" "$TMP_STATUS"' EXIT

CYCLE_ID=""

pass() {
  printf 'PASS %s\n' "$1"
}

fail() {
  printf 'FAIL %s\n' "$1" >&2
  exit 1
}

auth_header() {
  printf 'Authorization: Bearer %s' "$SUPERVISOR_TOKEN"
}

request() {
  local label="$1"
  local method="$2"
  local path="$3"
  local body="${4:-}"

  : > "$TMP_BODY"
  : > "$TMP_STATUS"

  if [[ -n "$body" ]]; then
    curl -sS -X "$method" \
      -H "$(auth_header)" \
      -H "Content-Type: application/json" \
      -d "$body" \
      -o "$TMP_BODY" \
      -w "%{http_code}" \
      "${BASE_URL}${path}" > "$TMP_STATUS"
  else
    curl -sS -X "$method" \
      -H "$(auth_header)" \
      -o "$TMP_BODY" \
      -w "%{http_code}" \
      "${BASE_URL}${path}" > "$TMP_STATUS"
  fi

  local status_code
  status_code="$(cat "$TMP_STATUS")"
  if [[ "$status_code" =~ ^2[0-9][0-9]$ ]]; then
    pass "$label"
  else
    printf 'Response body for %s:\n' "$label" >&2
    cat "$TMP_BODY" >&2
    fail "$label (HTTP ${status_code})"
  fi
}

request_no_auth() {
  local label="$1"
  local method="$2"
  local path="$3"

  : > "$TMP_BODY"
  : > "$TMP_STATUS"

  curl -sS -X "$method" \
    -o "$TMP_BODY" \
    -w "%{http_code}" \
    "${BASE_URL}${path}" > "$TMP_STATUS"

  local status_code
  status_code="$(cat "$TMP_STATUS")"
  if [[ "$status_code" =~ ^2[0-9][0-9]$ ]]; then
    pass "$label"
  else
    printf 'Response body for %s:\n' "$label" >&2
    cat "$TMP_BODY" >&2
    fail "$label (HTTP ${status_code})"
  fi
}

json_get() {
  local expression="$1"
  python3 - "$TMP_BODY" "$expression" <<'PY'
import json
import sys

body_path = sys.argv[1]
expression = sys.argv[2]

with open(body_path, "r", encoding="utf-8") as handle:
    data = json.load(handle)

value = data
for part in expression.split("."):
    if part == "":
        continue
    if isinstance(value, list):
        value = value[int(part)]
    else:
        value = value.get(part)

if value is None:
    print("")
elif isinstance(value, (dict, list)):
    print(json.dumps(value))
else:
    print(value)
PY
}

printf 'Patrol smoke base URL: %s\n' "$BASE_URL"
printf 'Patrol smoke route ID: %s\n' "$ROUTE_ID"

request_no_auth "health endpoint" "GET" "/health"
request "patrol status" "GET" "/patrol/status"
request "patrol routes" "GET" "/patrol/routes"
request "patrol zones" "GET" "/patrol/zones"
request "trigger patrol cycle" "POST" "/patrol/trigger" "{\"route_id\":\"${ROUTE_ID}\",\"triggered_by\":\"manual\"}"

CYCLE_ID="$(json_get "cycle_id")"
if [[ -z "$CYCLE_ID" ]]; then
  fail "capture cycle_id from /patrol/trigger response"
fi
pass "capture cycle_id"

request "get triggered cycle" "GET" "/patrol/cycles/${CYCLE_ID}"
request "list patrol cycles" "GET" "/patrol/cycles"
request "list patrol anomalies" "GET" "/patrol/anomalies"
request "suspend patrol" "POST" "/patrol/suspend" "{\"reason\":\"manual smoke suspension\"}"
request "resume patrol" "POST" "/patrol/resume"
request "trigger estop" "POST" "/estop"
request "release estop" "POST" "/estop/release"

printf 'PASS final cycle lookup remains non-blocking\n'
printf 'Final cycle_id: %s\n' "$CYCLE_ID"
