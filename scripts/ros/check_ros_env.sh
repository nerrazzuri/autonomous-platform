#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ros/_common.sh
source "$SCRIPT_DIR/_common.sh"

source_ros
print_ros_context

info "Checking ROS package visibility"
ros2 pkg list | grep '^robot_bringup$' >/dev/null || fail "robot_bringup package is not visible. Build/source wheeltec_ros2."
info "PASS robot_bringup package visible"

ros2 pkg list | grep '^lslidar_driver$' >/dev/null || fail "lslidar_driver package is not visible. Build/source wheeltec_ros2."
info "PASS lslidar_driver package visible"

ros2 pkg list | grep '^slam_toolbox$' >/dev/null || fail "slam_toolbox package is not visible. Install/source ROS2 slam_toolbox."
info "PASS slam_toolbox package visible"

ros2 pkg list | grep '^nav2_map_server$' >/dev/null || fail "nav2_map_server package is not visible. Install ROS2 navigation packages."
info "PASS nav2_map_server package visible"

info "PASS ROS commissioning environment is ready"
