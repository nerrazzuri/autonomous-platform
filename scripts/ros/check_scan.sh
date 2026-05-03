#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ros/_common.sh
source "$SCRIPT_DIR/_common.sh"

SCAN_TOPIC="${SCAN_TOPIC:-/scan}"
ONCE_TIMEOUT="${ONCE_TIMEOUT:-10}"
HZ_TIMEOUT="${HZ_TIMEOUT:-10}"

source_ros
print_ros_context

info "Available scan-like topics:"
ros2 topic list | grep scan || true

info "Waiting up to ${ONCE_TIMEOUT}s for one LaserScan on ${SCAN_TOPIC}"
if ! timeout "$ONCE_TIMEOUT" ros2 topic echo "$SCAN_TOPIC" --once; then
  fail "No LaserScan received. Check M10 power, Ethernet, IP, lslidar_driver, and scan topic."
fi

info "Measuring LaserScan rate on ${SCAN_TOPIC} for up to ${HZ_TIMEOUT}s"
if ! timeout "$HZ_TIMEOUT" ros2 topic hz "$SCAN_TOPIC"; then
  fail "LaserScan rate check failed. Check M10 power, Ethernet, IP, lslidar_driver, and scan topic."
fi

info "PASS LaserScan topic ${SCAN_TOPIC} is publishing"
