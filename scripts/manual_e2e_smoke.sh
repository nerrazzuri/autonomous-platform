#!/usr/bin/env bash

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
OPERATOR_TOKEN="${OPERATOR_TOKEN:-change-me-operator}"
SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN:-change-me-supervisor}"

pass() {
  printf 'PASS: %s\n' "$1"
}

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

contains_status() {
  local status="$1"
  shift
  local allowed
  for allowed in "$@"; do
    if [[ "$status" == "$allowed" ]]; then
      return 0
    fi
  done
  return 1
}

request_json() {
  local label="$1"
  local method="$2"
  local path="$3"
  local token="$4"
  local data="$5"
  shift 5
  local expected_statuses=("$@")
  local response_file
  response_file="$(mktemp)"
  local status=""
  local body=""

  local curl_args=(
    -sS
    -X "$method"
    -H "Accept: application/json"
    -o "$response_file"
    -w "%{http_code}"
  )

  if [[ -n "$token" ]]; then
    curl_args+=(-H "Authorization: Bearer $token")
  fi

  if [[ -n "$data" ]]; then
    curl_args+=(-H "Content-Type: application/json" --data "$data")
  fi

  if ! status="$(curl "${curl_args[@]}" "${BASE_URL}${path}")"; then
    rm -f "$response_file"
    fail "${label} request failed for ${method} ${path}"
  fi

  body="$(cat "$response_file")"
  rm -f "$response_file"

  if ! contains_status "$status" "${expected_statuses[@]}"; then
    printf 'Response body:\n%s\n' "$body" >&2
    fail "${label} expected HTTP ${expected_statuses[*]} but got ${status}"
  fi

  pass "${label} (${method} ${path} -> ${status})"
  printf '%s' "$body"
}

parse_json_field() {
  local json_input="$1"
  local field_name="$2"
  printf '%s' "$json_input" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
value = payload.get(sys.argv[1], "")
if isinstance(value, (dict, list)):
    raise SystemExit(1)
print(value)
' "$field_name"
}

printf 'Running manual Phase 1 smoke test against %s\n' "$BASE_URL"

health_response="$(request_json "Health check" "GET" "/health" "" "" "200")"
if ! printf '%s' "$health_response" | grep -q '"status"'; then
  fail "Health check response did not include a status field"
fi

request_json "Quadruped status" "GET" "/quadruped/status" "$OPERATOR_TOKEN" "" "200" >/dev/null
request_json "Queue status" "GET" "/queue/status" "$OPERATOR_TOKEN" "" "200" >/dev/null

task_response="$(request_json \
  "Create task" \
  "POST" \
  "/tasks" \
  "$OPERATOR_TOKEN" \
  '{"station_id":"A","destination_id":"QA","priority":1,"notes":"Manual E2E smoke test"}' \
  "200")"

task_id="$(parse_json_field "$task_response" "id" || true)"
if [[ -z "$task_id" ]]; then
  fail "Could not parse task id from /tasks response"
fi
pass "Captured task id ${task_id}"

request_json "Confirm load" "POST" "/tasks/${task_id}/confirm-load" "$OPERATOR_TOKEN" "" "200" >/dev/null
request_json "Confirm unload" "POST" "/tasks/${task_id}/confirm-unload" "$OPERATOR_TOKEN" "" "200" >/dev/null
request_json "List tasks" "GET" "/tasks" "$OPERATOR_TOKEN" "" "200" >/dev/null
request_json "List routes" "GET" "/routes" "$SUPERVISOR_TOKEN" "" "200" >/dev/null

# In Phase 1, estop endpoints may return 503 when no SDK session is initialized yet.
request_json "Emergency stop" "POST" "/estop" "$OPERATOR_TOKEN" "" "200" "503" >/dev/null
request_json "Release e-stop" "POST" "/estop/release" "$SUPERVISOR_TOKEN" "" "200" "503" >/dev/null

printf 'PASS: Manual Phase 1 end-to-end smoke test completed\n'
