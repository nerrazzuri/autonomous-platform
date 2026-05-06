from __future__ import annotations

"""Patrol-specific alert rules registered from the app layer."""

from apps.patrol import events as patrol_events
from shared.core.event_bus import Event
from shared.observability.alerts import Alert, register_alert_rule


def _patrol_cycle_failed_alert(event: Event) -> Alert:
    payload = dict(event.payload or {})
    raw_reason = payload.get("reason")
    message = raw_reason.strip() if isinstance(raw_reason, str) and raw_reason.strip() else "Patrol cycle failed"
    return Alert(
        alert_id=event.event_id,
        timestamp=event.timestamp.isoformat().replace("+00:00", "Z"),
        severity="error",
        source="patrol",
        event_type="patrol_cycle_failed",
        message=message,
        robot_id=payload.pop("robot_id", None),
        task_id=payload.pop("task_id", None) or event.task_id,
        cycle_id=payload.pop("cycle_id", None) or event.task_id,
        route_id=payload.pop("route_id", None),
        job_id=payload.pop("job_id", None),
        metadata=payload,
    )


def register_patrol_alert_rules() -> None:
    register_alert_rule(
        event_name=patrol_events.PATROL_CYCLE_FAILED,
        alert_type="patrol_cycle_failed",
        default_message="Patrol cycle failed",
        severity="error",
        source="patrol",
        builder=_patrol_cycle_failed_alert,
    )


__all__ = ["register_patrol_alert_rules"]
