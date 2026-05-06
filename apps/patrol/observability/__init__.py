"""Patrol-owned observability registrations."""

from apps.patrol.observability.alerts import register_patrol_alert_rules
from apps.patrol.observability.websocket import register_patrol_websocket_events

__all__ = ["register_patrol_alert_rules", "register_patrol_websocket_events"]
