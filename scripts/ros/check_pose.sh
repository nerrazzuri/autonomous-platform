#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ros/_common.sh
source "$SCRIPT_DIR/_common.sh"

POSE_TOPIC="${POSE_TOPIC:-/pose}"
ONCE_TIMEOUT="${ONCE_TIMEOUT:-10}"

source_ros
print_ros_context

info "Available pose-like topics:"
ros2 topic list | grep pose || true

info "Waiting up to ${ONCE_TIMEOUT}s for one pose message on ${POSE_TOPIC}"
if ! timeout "$ONCE_TIMEOUT" ros2 topic echo "$POSE_TOPIC" --once; then
  fail "No pose received. Check slam_toolbox localization, map_file, /scan, /odom, and TF."
fi

info "PASS pose topic ${POSE_TOPIC} is publishing"
