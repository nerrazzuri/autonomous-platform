from __future__ import annotations

"""Logistics-specific alert rules registered from the app layer."""

from shared.core.event_bus import Event, EventName
from shared.observability.alerts import Alert, register_alert_rule


def _task_failed_alert(event: Event) -> Alert:
    payload = dict(event.payload or {})
    raw_message = payload.get("notes")
    message = raw_message.strip() if isinstance(raw_message, str) and raw_message.strip() else "Logistics task failed"
    return Alert(
        alert_id=event.event_id,
        timestamp=event.timestamp.isoformat().replace("+00:00", "Z"),
        severity="error",
        source="dispatcher",
        event_type="logistics_task_failed",
        message=message,
        robot_id=payload.pop("robot_id", None),
        task_id=payload.pop("task_id", None) or event.task_id,
        metadata=payload,
    )


def register_logistics_alert_rules() -> None:
    register_alert_rule(
        event_name=EventName.TASK_FAILED,
        alert_type="logistics_task_failed",
        default_message="Logistics task failed",
        severity="error",
        source="dispatcher",
        builder=_task_failed_alert,
    )


__all__ = ["register_logistics_alert_rules"]
