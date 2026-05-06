from __future__ import annotations

"""Patrol-specific WebSocket event forwarding registrations."""

from shared.api.ws_broker import register_websocket_forwarding_event
from apps.patrol import events as patrol_events


_PATROL_WEBSOCKET_EVENTS = (
    patrol_events.PATROL_CYCLE_STARTED,
    patrol_events.PATROL_CYCLE_COMPLETED,
    patrol_events.PATROL_CYCLE_FAILED,
    patrol_events.PATROL_WAYPOINT_OBSERVED,
    patrol_events.PATROL_ANOMALY_DETECTED,
    patrol_events.PATROL_ANOMALY_CLEARED,
    patrol_events.PATROL_SUSPENDED,
    patrol_events.PATROL_RESUMED,
)


def register_patrol_websocket_events() -> None:
    for event_name in _PATROL_WEBSOCKET_EVENTS:
        register_websocket_forwarding_event(event_name)


__all__ = ["register_patrol_websocket_events"]
