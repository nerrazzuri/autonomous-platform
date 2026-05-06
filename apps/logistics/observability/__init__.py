"""Logistics-owned observability registrations."""

from apps.logistics.observability.alerts import register_logistics_alert_rules
from apps.logistics.observability.status import register_logistics_status_provider
from apps.logistics.observability.websocket import register_logistics_websocket_events

__all__ = [
    "register_logistics_alert_rules",
    "register_logistics_status_provider",
    "register_logistics_websocket_events",
]
