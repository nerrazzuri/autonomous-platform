#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ros/_common.sh
source "$SCRIPT_DIR/_common.sh"

MAP_ARG="${1:-}"
SCAN_TOPIC="${SCAN_TOPIC:-/scan}"
START_LIDAR="${START_LIDAR:-false}"
USE_SIM_TIME="${USE_SIM_TIME:-false}"
MAP_DIR="${MAP_DIR:-$WHEELTEC_WS/src/robot_bringup/maps}"

[[ -n "$MAP_ARG" ]] || fail "Map file or map base name required. Usage: ./scripts/ros/start_localization.sh facility_map"

source_ros
print_ros_context

if [[ "$MAP_ARG" == *.yaml ]]; then
  MAP_FILE="$MAP_ARG"
else
  MAP_FILE="${MAP_DIR}/${MAP_ARG}.yaml"
fi

if [[ ! -f "$MAP_FILE" ]]; then
  if [[ "$DRY_RUN" == "1" ]]; then
    warn "Map file not found during dry run: $MAP_FILE"
  else
    fail "Map file not found: $MAP_FILE"
  fi
fi

run_or_echo ros2 launch robot_bringup localization.launch.py \
  map_file:="$MAP_FILE" \
  scan_topic:="$SCAN_TOPIC" \
  start_lidar:="$START_LIDAR" \
  use_sim_time:="$USE_SIM_TIME"
