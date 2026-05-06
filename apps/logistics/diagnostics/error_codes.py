"""Logistics workflow diagnostic error-code taxonomy."""

from __future__ import annotations

# Route / station / waypoint workflow
ROUTE_NOT_FOUND = "route.not_found"
ROUTE_PLACEHOLDER_BLOCKED = "route.placeholder_blocked"
ROUTE_EMPTY_WAYPOINTS = "route.empty_waypoints"
ROUTE_INVALID_STATION = "route.invalid_station"
WAYPOINT_TIMEOUT = "waypoint.timeout"
WAYPOINT_REACHED = "waypoint.reached"

# Task / dispatcher workflow
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

# HMI / TJC / commissioning workflow
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

# Audio / speaker workflow
AUDIO_FILE_MISSING = "audio.file_missing"
AUDIO_PLAYER_MISSING = "audio.player_missing"
AUDIO_PLAYBACK_FAILED = "audio.playback_failed"

SUGGESTED_ACTIONS: dict[str, str] = {
    ROUTE_NOT_FOUND: "Select a configured route or capture the route before dispatching.",
    ROUTE_PLACEHOLDER_BLOCKED: "Run commissioning and replace placeholder route data before demo operation.",
    HMI_TOKEN_INVALID: "Reopen the HMI with a valid role token from the local runtime configuration.",
    AUDIO_FILE_MISSING: "Verify the configured audio file path exists on the workstation.",
}


def get_suggested_action(error_code: str | None) -> str | None:
    if error_code is None:
        return None
    return SUGGESTED_ACTIONS.get(error_code)
