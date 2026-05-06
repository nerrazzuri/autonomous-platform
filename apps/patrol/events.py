from __future__ import annotations

"""Patrol-owned event names for app workflow semantics."""

PATROL_CYCLE_STARTED = "patrol.cycle_started"
PATROL_WAYPOINT_OBSERVED = "patrol.waypoint_observed"
PATROL_CYCLE_COMPLETED = "patrol.cycle_completed"
PATROL_CYCLE_FAILED = "patrol.cycle_failed"
PATROL_ANOMALY_DETECTED = "patrol.anomaly_detected"
PATROL_ANOMALY_CLEARED = "patrol.anomaly_cleared"
PATROL_SUSPENDED = "patrol.suspended"
PATROL_RESUMED = "patrol.resumed"


__all__ = [
    "PATROL_CYCLE_STARTED",
    "PATROL_WAYPOINT_OBSERVED",
    "PATROL_CYCLE_COMPLETED",
    "PATROL_CYCLE_FAILED",
    "PATROL_ANOMALY_DETECTED",
    "PATROL_ANOMALY_CLEARED",
    "PATROL_SUSPENDED",
    "PATROL_RESUMED",
]
