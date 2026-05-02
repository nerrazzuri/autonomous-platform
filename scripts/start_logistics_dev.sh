#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

APP_CONFIG="${APP_CONFIG:-apps/logistics/config/logistics_demo_config.yaml}"
START_ROS_STACK="${START_ROS_STACK:-0}"
ROS_LAUNCH_ARGS="${ROS_LAUNCH_ARGS:-start_lidar:=false}"
DRY_RUN="${DRY_RUN:-0}"
ALLOW_NON_PY310="${ALLOW_NON_PY310:-0}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
ROS_PID=""

fail() {
  printf 'ERROR %s\n' "$1" >&2
  exit 1
}

cleanup() {
  if [[ -n "$ROS_PID" ]]; then
    printf 'Stopping ROS localization stack pid %s\n' "$ROS_PID"
    kill "$ROS_PID" >/dev/null 2>&1 || true
    wait "$ROS_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if [[ -f "$VENV_DIR/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  PYTHON_BIN="${PYTHON_BIN:-python}"
elif [[ -x "$VENV_DIR/bin/python" ]]; then
  printf 'WARN .venv activation script missing at %s; using .venv/bin/python directly\n' "$VENV_DIR/bin/activate"
  PYTHON_BIN="${PYTHON_BIN:-$VENV_DIR/bin/python}"
else
  printf 'WARN .venv not found at %s; using python3.10 directly\n' "$VENV_DIR"
  PYTHON_BIN="${PYTHON_BIN:-python3.10}"
fi

command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "Python executable not found: $PYTHON_BIN"

PYTHON_VERSION="$("$PYTHON_BIN" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
PY
)"

if [[ "$PYTHON_VERSION" != 3.10.* && "$ALLOW_NON_PY310" != "1" ]]; then
  fail "Python 3.10 is required for the Agibot SDK; got $PYTHON_VERSION. Set ALLOW_NON_PY310=1 only for non-SDK diagnostics."
fi

[[ -f "$APP_CONFIG" ]] || fail "Config file not found: $APP_CONFIG"
export QUADRUPED_CONFIG_PATH="${QUADRUPED_CONFIG_PATH:-$APP_CONFIG}"

if ! "$PYTHON_BIN" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  if [[ "$DRY_RUN" == "1" ]]; then
    printf 'WARN Python dependencies missing for %s; backend would not start until requirements are installed\n' "$PYTHON_BIN"
  else
    fail "Python dependencies missing. Install with: $PYTHON_BIN -m pip install -r requirements.txt"
  fi
fi

printf 'Project root: %s\n' "$ROOT_DIR"
printf 'Python: %s (%s)\n' "$PYTHON_BIN" "$PYTHON_VERSION"
printf 'Config path: %s\n' "$QUADRUPED_CONFIG_PATH"
printf 'ROS_DISTRO: %s\n' "${ROS_DISTRO:-not-sourced}"

start_ros_stack() {
  [[ "$START_ROS_STACK" == "1" ]] || return 0

  local ros_setup="/opt/ros/humble/setup.bash"
  local wheeltec_setup="/home/liang/Projects/wheeltec_ros2/install/setup.bash"

  [[ -f "$ros_setup" ]] || fail "START_ROS_STACK=1 but missing $ros_setup"
  [[ -f "$wheeltec_setup" ]] || fail "START_ROS_STACK=1 but missing $wheeltec_setup"

  printf 'ROS localization command: ros2 launch robot_bringup localization.launch.py %s\n' "$ROS_LAUNCH_ARGS"
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi

  # shellcheck disable=SC1090
  source "$ros_setup"
  # shellcheck disable=SC1090
  source "$wheeltec_setup"

  read -r -a ros_args <<< "$ROS_LAUNCH_ARGS"
  ros2 launch robot_bringup localization.launch.py "${ros_args[@]}" &
  ROS_PID="$!"
  printf 'Started ROS localization stack pid %s\n' "$ROS_PID"
}

start_ros_stack

BACKEND_CMD=("$PYTHON_BIN" "main.py")
printf 'Backend command:'
printf ' %q' "${BACKEND_CMD[@]}"
printf '\n'

if [[ "$DRY_RUN" == "1" ]]; then
  printf 'DRY_RUN=1 set; backend not started.\n'
  exit 0
fi

exec "${BACKEND_CMD[@]}"
