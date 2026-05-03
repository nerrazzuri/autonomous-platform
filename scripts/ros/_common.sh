#!/usr/bin/env bash

WHEELTEC_WS="${WHEELTEC_WS:-/home/liang/Projects/wheeltec_ros2}"
ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
WHEELTEC_SETUP="${WHEELTEC_WS}/install/setup.bash"
DRY_RUN="${DRY_RUN:-0}"

fail() {
  printf 'ERROR %s\n' "$1" >&2
  exit 1
}

warn() {
  printf 'WARN %s\n' "$1" >&2
}

info() {
  printf 'INFO %s\n' "$1"
}

require_file() {
  local path="$1"
  [[ -f "$path" ]] || fail "Required file missing: $path"
}

require_dir() {
  local path="$1"
  [[ -d "$path" ]] || fail "Required directory missing: $path"
}

require_command() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1 || fail "Required command not found: $command_name"
}

source_ros() {
  require_file "$ROS_SETUP"
  require_dir "$WHEELTEC_WS"
  require_file "$WHEELTEC_SETUP"

  local restore_nounset=0
  case "$-" in
    *u*) restore_nounset=1 ;;
  esac
  set +u
  # shellcheck disable=SC1090
  source "$ROS_SETUP"
  # shellcheck disable=SC1090
  source "$WHEELTEC_SETUP"
  if [[ "$restore_nounset" == "1" ]]; then
    set -u
  fi

  require_command ros2
}

print_ros_context() {
  info "WHEELTEC_WS=$WHEELTEC_WS"
  info "ROS_DISTRO=${ROS_DISTRO:-not-sourced}"
}

run_or_echo() {
  info "Command: $*"
  if [[ "$DRY_RUN" == "1" ]]; then
    info "DRY_RUN=1 set; command not executed."
    return 0
  fi
  "$@"
}
