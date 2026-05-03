#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ros/_common.sh
source "$SCRIPT_DIR/_common.sh"

ODOM_FRAME="${ODOM_FRAME:-odom}"
BASE_FRAME="${BASE_FRAME:-BASE_LINK}"
TF_TIMEOUT="${TF_TIMEOUT:-10}"

source_ros
print_ros_context

info "Waiting up to ${TF_TIMEOUT}s for TF ${ODOM_FRAME} -> ${BASE_FRAME}"
if ! timeout "$TF_TIMEOUT" ros2 run tf2_ros tf2_echo "$ODOM_FRAME" "$BASE_FRAME"; then
  fail "TF odom -> BASE_LINK not available. Check autonomous-platform ROS2 bridge and odometry publisher."
fi

info "PASS TF ${ODOM_FRAME} -> ${BASE_FRAME} is available"
