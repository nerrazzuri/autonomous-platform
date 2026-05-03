#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail_count=0
warn_count=0

pass() {
  printf 'PASS %s\n' "$1"
}

warn() {
  warn_count=$((warn_count + 1))
  printf 'WARN %s\n' "$1"
}

fail() {
  fail_count=$((fail_count + 1))
  printf 'FAIL %s\n' "$1"
}

check_file() {
  local path="$1"
  if [[ -e "$path" ]]; then
    pass "required file exists: $path"
  else
    fail "required file missing: $path"
  fi
}

printf 'Runtime environment check for %s\n' "$ROOT_DIR"

if command -v python3.10 >/dev/null 2>&1; then
  pass "python3.10 available: $(python3.10 --version 2>&1)"
else
  fail "python3.10 is required but was not found"
fi

if [[ -d .venv ]]; then
  pass ".venv exists"
else
  warn ".venv not found; scripts can still use python3.10 directly"
fi

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  active_version="$(python - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
PY
)"
  if [[ "$active_version" == 3.10.* ]]; then
    pass "active virtualenv uses Python $active_version"
  else
    warn "active virtualenv uses Python $active_version, expected 3.10.x"
  fi
else
  warn "no active virtualenv detected"
fi

check_file "config.yaml.example"
check_file "data/logistics_routes.json"
check_file "apps/logistics/api/hmi.py"
check_file "apps/hmi_agent/protocol.py"

APP_CONFIG="${APP_CONFIG:-apps/logistics/config/logistics_demo_config.yaml}"
PLACEHOLDER_TOKEN_PATTERN='(__OPERATOR_TOKEN__|__SUPERVISOR_TOKEN__|__QA_TOKEN__|change-me-operator|change-me-supervisor|change-me-qa)'
if [[ -f "$APP_CONFIG" ]]; then
  if grep -Eq "$PLACEHOLDER_TOKEN_PATTERN" "$APP_CONFIG"; then
    warn "auth token placeholders detected in $APP_CONFIG; create a local uncommitted config with real tokens before runtime"
  else
    pass "auth token placeholders not detected in $APP_CONFIG"
  fi
else
  warn "auth token placeholder check skipped; config not found: $APP_CONFIG"
fi

if [[ -f data/audio/arrival.wav ]]; then
  pass "optional arrival audio exists: data/audio/arrival.wav"
else
  warn "optional arrival audio missing: data/audio/arrival.wav"
fi

if command -v aplay >/dev/null 2>&1; then
  pass "optional aplay command available"
else
  warn "optional aplay command not found"
fi

if [[ -f /opt/ros/humble/setup.bash ]]; then
  pass "optional ROS2 Humble setup exists"
else
  warn "optional ROS2 Humble setup not found at /opt/ros/humble/setup.bash"
fi

if command -v ros2 >/dev/null 2>&1; then
  pass "optional ros2 command available"
else
  warn "optional ros2 command not currently available"
fi

WHEELTEC_ROOT="/home/liang/Projects/wheeltec_ros2"
if [[ -f "$WHEELTEC_ROOT/install/setup.bash" ]]; then
  pass "optional wheeltec install setup exists"
else
  warn "optional wheeltec install setup missing: $WHEELTEC_ROOT/install/setup.bash"
fi

if [[ -d "$WHEELTEC_ROOT/src/robot_bringup" ]]; then
  pass "optional robot_bringup package source exists"
else
  warn "optional robot_bringup package source missing"
fi

if [[ -e /dev/tjc_hmi ]]; then
  pass "optional TJC device exists: /dev/tjc_hmi"
else
  warn "optional TJC device /dev/tjc_hmi not found"
fi

shopt -s nullglob
tty_devices=(/dev/ttyUSB*)
shopt -u nullglob
if ((${#tty_devices[@]} > 0)); then
  pass "optional USB serial devices detected: ${tty_devices[*]}"
else
  warn "optional USB serial devices not detected: /dev/ttyUSB*"
fi

if [[ -d .git ]]; then
  branch="$(git branch --show-current 2>/dev/null || true)"
  status_count="$(git status --short 2>/dev/null | wc -l | tr -d ' ')"
  pass "git repository detected on branch: ${branch:-unknown}"
  if [[ "$status_count" == "0" ]]; then
    pass "git working tree clean"
  else
    warn "git working tree has $status_count changed/untracked path(s)"
  fi
else
  warn "not running inside a git repository"
fi

printf 'Summary: %s failure(s), %s warning(s)\n' "$fail_count" "$warn_count"
if ((fail_count > 0)); then
  exit 1
fi
