from __future__ import annotations

"""Patrol-specific WebSocket event forwarding registrations."""

from shared.api.ws_broker import register_websocket_forwarding_event
from shared.core.event_bus import EventName


_PATROL_WEBSOCKET_EVENTS = (
    EventName.PATROL_CYCLE_STARTED,
    EventName.PATROL_CYCLE_COMPLETED,
    EventName.PATROL_CYCLE_FAILED,
    EventName.PATROL_WAYPOINT_OBSERVED,
    EventName.PATROL_ANOMALY_DETECTED,
    EventName.PATROL_ANOMALY_CLEARED,
    EventName.PATROL_SUSPENDED,
    EventName.PATROL_RESUMED,
)


def register_patrol_websocket_events() -> None:
    for event_name in _PATROL_WEBSOCKET_EVENTS:
        register_websocket_forwarding_event(event_name)


__all__ = ["register_patrol_websocket_events"]
