"""Diagnostic error code taxonomy for operator-facing diagnosis."""

from __future__ import annotations

# SDK
SDK_IMPORT_FAILED = "sdk.import_failed"
SDK_LIB_NOT_FOUND = "sdk.lib_not_found"
SDK_ABI_MISMATCH = "sdk.abi_mismatch"
SDK_CONNECT_FAILED = "sdk.connect_failed"
SDK_CONNECTION_LOST = "sdk.connection_lost"
SDK_CHECK_CONNECT_FAILED = "sdk.check_connect_failed"
SDK_TELEMETRY_STALE = "sdk.telemetry_stale"
SDK_POSITION_INVALID = "sdk.position_invalid"
SDK_COMMAND_FAILED = "sdk.command_failed"
SDK_ESTOP_FAILED = "sdk.estop_failed"

# Network
NETWORK_ROBOT_UNREACHABLE = "network.robot_unreachable"
NETWORK_LIDAR_UNREACHABLE = "network.lidar_unreachable"
NETWORK_PORT_CLOSED = "network.port_closed"
NETWORK_INTERFACE_MISMATCH = "network.interface_mismatch"
NETWORK_WIFI_DROPPED = "network.wifi_dropped"

# ROS2 / LiDAR / localization
ROS2_NOT_SOURCED = "ros2.not_sourced"
ROS2_BRIDGE_NOT_STARTED = "ros2.bridge_not_started"
ROS2_NODE_INIT_FAILED = "ros2.node_init_failed"
LIDAR_SCAN_TIMEOUT = "lidar.scan_timeout"
LIDAR_SCAN_TOPIC_MISSING = "lidar.scan_topic_missing"
LIDAR_SCAN_FRAME_MISMATCH = "lidar.scan_frame_mismatch"
LIDAR_IP_UNREACHABLE = "lidar.ip_unreachable"
ODOM_TIMEOUT = "odom.timeout"
TF_MISSING_ODOM_BASE = "tf.missing_odom_base"
LOCALIZATION_NOT_RUNNING = "localization.not_running"
LOCALIZATION_POSE_TIMEOUT = "localization.pose_timeout"
LOCALIZATION_CONFIDENCE_LOW = "localization.confidence_low"
MAP_FILE_MISSING = "map.file_missing"
MAP_LOAD_FAILED = "map.load_failed"

# Navigation
NAVIGATION_STARTED = "navigation.started"
NAVIGATION_BLOCKED = "navigation.blocked"
NAVIGATION_RESUMED = "navigation.resumed"
NAVIGATION_FAILED = "navigation.failed"

# Obstacle
OBSTACLE_DETECTED = "obstacle.detected"
OBSTACLE_CLEARED = "obstacle.cleared"
OBSTACLE_AUTO_RESUME_STARTED = "obstacle.auto_resume_started"
OBSTACLE_AUTO_RESUME_CANCELLED = "obstacle.auto_resume_cancelled"
OBSTACLE_REPEATED_MANUAL_REQUIRED = "obstacle.repeated_manual_required"
OBSTACLE_FALSE_POSITIVE_SUSPECTED = "obstacle.false_positive_suspected"

# Battery
BATTERY_LOW = "battery.low"
BATTERY_CRITICAL = "battery.critical"
BATTERY_RECOVERED = "battery.recovered"

# Process lifecycle
PROCESS_START_FAILED = "process.start_failed"
PROCESS_EXITED_NONZERO = "process.exited_nonzero"
PROCESS_LOG_CAPTURE_FAILED = "process.log_capture_failed"

# Config / startup
CONFIG_FILE_MISSING = "config.file_missing"
CONFIG_PLACEHOLDER_TOKEN = "config.placeholder_token"
CONFIG_INVALID_IP = "config.invalid_ip"
CONFIG_SDK_PATH_INVALID = "config.sdk_path_invalid"
CONFIG_ROS2_DISABLED = "config.ros2_disabled"
STARTUP_FAILED = "startup.failed"

SUGGESTED_ACTIONS: dict[str, str] = {
    SDK_CONNECT_FAILED: "Check robot power, SDK IP/port, local IP binding, and network reachability.",
    NETWORK_ROBOT_UNREACHABLE: "Ping the robot IP and verify the workstation is on the robot deployment network.",
    NETWORK_LIDAR_UNREACHABLE: "Check LiDAR power, Ethernet cabling, IP address, and subnet configuration.",
    LIDAR_SCAN_TIMEOUT: "Confirm the LiDAR driver is running and the /scan topic is publishing.",
    LOCALIZATION_POSE_TIMEOUT: "Check localization startup, map availability, and /pose publication.",
    TF_MISSING_ODOM_BASE: "Inspect TF publication between odom and BASE_LINK before navigation.",
    OBSTACLE_REPEATED_MANUAL_REQUIRED: "Ask an operator to inspect the path and clear or classify the repeated obstacle.",
    BATTERY_CRITICAL: "Charge or dock the robot and confirm battery level recovers before continuing operation.",
    PROCESS_START_FAILED: "Check the command path, executable permissions, environment sourcing, and working directory.",
    PROCESS_EXITED_NONZERO: "Inspect the captured stdout/stderr logs for the process failure reason.",
    CONFIG_PLACEHOLDER_TOKEN: "Replace placeholder tokens in local configuration before enabling protected controls.",
}


def get_suggested_action(error_code: str | None) -> str | None:
    if error_code is None:
        return None
    return SUGGESTED_ACTIONS.get(error_code)
