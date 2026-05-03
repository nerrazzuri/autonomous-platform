#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ros/_common.sh
source "$SCRIPT_DIR/_common.sh"

MAP_NAME="${1:-}"
MAP_OUTPUT_DIR="${MAP_OUTPUT_DIR:-$WHEELTEC_WS/src/robot_bringup/maps}"

[[ -n "$MAP_NAME" ]] || fail "Map name required. Usage: ./scripts/ros/save_map.sh facility_map"
[[ "$MAP_NAME" != *.yaml ]] || fail "Pass a map base name without .yaml for save_map.sh"

source_ros
print_ros_context

MAP_BASE="${MAP_OUTPUT_DIR}/${MAP_NAME}"
info "Map output base: $MAP_BASE"

if [[ "$DRY_RUN" == "1" ]]; then
  run_or_echo ros2 run nav2_map_server map_saver_cli -f "$MAP_BASE"
  info "Would save: ${MAP_BASE}.yaml"
  info "Would save: ${MAP_BASE}.pgm"
  exit 0
fi

mkdir -p "$MAP_OUTPUT_DIR"
info "Command: ros2 run nav2_map_server map_saver_cli -f $MAP_BASE"
ros2 run nav2_map_server map_saver_cli -f "$MAP_BASE"

[[ -f "${MAP_BASE}.yaml" ]] || fail "Map YAML was not created: ${MAP_BASE}.yaml"
[[ -f "${MAP_BASE}.pgm" ]] || fail "Map PGM was not created: ${MAP_BASE}.pgm"

ls -l "${MAP_BASE}.yaml" "${MAP_BASE}.pgm"
info "Commit the .yaml and .pgm only after confirming this is the correct factory map."
