from __future__ import annotations

"""Logistics-specific WebSocket event forwarding registrations."""

from shared.api.ws_broker import register_websocket_forwarding_event
from shared.core.event_bus import EventName


_LOGISTICS_WEBSOCKET_EVENTS = (
    EventName.TASK_STATUS_CHANGED,
    EventName.TASK_SUBMITTED,
    EventName.TASK_DISPATCHED,
    EventName.TASK_COMPLETED,
    EventName.TASK_FAILED,
    EventName.TASK_CANCELLED,
)


def register_logistics_websocket_events() -> None:
    for event_name in _LOGISTICS_WEBSOCKET_EVENTS:
        register_websocket_forwarding_event(event_name)


__all__ = ["register_logistics_websocket_events"]
