#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ros/_common.sh
source "$SCRIPT_DIR/_common.sh"

SCAN_TOPIC="${SCAN_TOPIC:-/scan}"
START_LIDAR="${START_LIDAR:-false}"
USE_SIM_TIME="${USE_SIM_TIME:-false}"

source_ros
print_ros_context

run_or_echo ros2 launch robot_bringup mapping.launch.py \
  scan_topic:="$SCAN_TOPIC" \
  start_lidar:="$START_LIDAR" \
  use_sim_time:="$USE_SIM_TIME"
