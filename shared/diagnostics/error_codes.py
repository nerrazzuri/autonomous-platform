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

# Navigation / route
ROUTE_NOT_FOUND = "route.not_found"
ROUTE_PLACEHOLDER_BLOCKED = "route.placeholder_blocked"
ROUTE_EMPTY_WAYPOINTS = "route.empty_waypoints"
ROUTE_INVALID_STATION = "route.invalid_station"
NAVIGATION_STARTED = "navigation.started"
NAVIGATION_BLOCKED = "navigation.blocked"
NAVIGATION_RESUMED = "navigation.resumed"
NAVIGATION_FAILED = "navigation.failed"
WAYPOINT_TIMEOUT = "waypoint.timeout"
WAYPOINT_REACHED = "waypoint.reached"

# Obstacle
OBSTACLE_DETECTED = "obstacle.detected"
OBSTACLE_CLEARED = "obstacle.cleared"
OBSTACLE_AUTO_RESUME_STARTED = "obstacle.auto_resume_started"
OBSTACLE_AUTO_RESUME_CANCELLED = "obstacle.auto_resume_cancelled"
OBSTACLE_REPEATED_MANUAL_REQUIRED = "obstacle.repeated_manual_required"
OBSTACLE_FALSE_POSITIVE_SUSPECTED = "obstacle.false_positive_suspected"

# Task / dispatcher
TASK_SUBMITTED = "task.submitted"
TASK_DISPATCHED = "task.dispatched"
TASK_WAITING_LOAD_CONFIRMATION = "task.waiting_load_confirmation"
TASK_WAITING_UNLOAD_CONFIRMATION = "task.waiting_unload_confirmation"
TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"
TASK_CANCELLED = "task.cancelled"
DISPATCHER_PAUSED = "dispatcher.paused"
DISPATCHER_RESUMED = "dispatcher.resumed"
DISPATCHER_NO_AVAILABLE_ROBOT = "dispatcher.no_available_robot"

# HMI / TJC / commissioning
HMI_ACTION_RECEIVED = "hmi.action_received"
HMI_ACTION_REJECTED = "hmi.action_rejected"
HMI_TOKEN_INVALID = "hmi.token_invalid"
TJC_SERIAL_PORT_MISSING = "tjc.serial_port_missing"
TJC_FRAME_PARSE_FAILED = "tjc.frame_parse_failed"
TJC_WEBSOCKET_DISCONNECTED = "tjc.websocket_disconnected"
COMMISSIONING_POSE_UNAVAILABLE = "commissioning.pose_unavailable"
COMMISSIONING_STATION_MARKED = "commissioning.station_marked"
COMMISSIONING_WAYPOINT_ADDED = "commissioning.waypoint_added"
COMMISSIONING_ROUTE_READY = "commissioning.route_ready"

# Audio / speaker
AUDIO_FILE_MISSING = "audio.file_missing"
AUDIO_PLAYER_MISSING = "audio.player_missing"
AUDIO_PLAYBACK_FAILED = "audio.playback_failed"

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
    ROUTE_NOT_FOUND: "Select a configured route or capture the route before dispatching.",
    ROUTE_PLACEHOLDER_BLOCKED: "Run commissioning and replace placeholder route data before demo operation.",
    OBSTACLE_REPEATED_MANUAL_REQUIRED: "Ask an operator to inspect the path and clear or classify the repeated obstacle.",
    HMI_TOKEN_INVALID: "Reopen the HMI with a valid role token from the local runtime configuration.",
    CONFIG_PLACEHOLDER_TOKEN: "Replace placeholder tokens in local configuration before enabling protected controls.",
    AUDIO_FILE_MISSING: "Verify the configured audio file path exists on the workstation.",
}


def get_suggested_action(error_code: str | None) -> str | None:
    if error_code is None:
        return None
    return SUGGESTED_ACTIONS.get(error_code)
