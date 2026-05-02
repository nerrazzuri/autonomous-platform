from __future__ import annotations

"""In-process alert normalization, routing, and read-only access."""

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
import threading
from typing import Any
from uuid import uuid4

from shared.api.ws_broker import WebSocketBroker, get_ws_broker
from shared.audit import audit_event
from shared.core.event_bus import Event, EventBus, EventName, get_event_bus
from shared.core.logger import MASKED_VALUE, get_logger, redact_sensitive


logger = get_logger(__name__)

_VALID_SEVERITIES = {"info", "warning", "error", "critical"}
_DEFAULT_MAX_ALERTS = 1000
_ALERT_SUBSCRIPTIONS = (
    EventName.SYSTEM_ALERT,
    EventName.BATTERY_CRITICAL,
    EventName.TASK_FAILED,
    EventName.ESTOP_TRIGGERED,
    EventName.ESTOP_RELEASED,
    EventName.PATROL_CYCLE_FAILED,
)
_DEFAULT_ALERT_MESSAGES = {
    EventName.BATTERY_CRITICAL: "Battery level is critical",
    EventName.TASK_FAILED: "Logistics task failed",
    EventName.ESTOP_TRIGGERED: "Emergency stop triggered",
    EventName.ESTOP_RELEASED: "Emergency stop released",
    EventName.PATROL_CYCLE_FAILED: "Patrol cycle failed",
}
_DEFAULT_ALERT_SOURCES = {
    EventName.BATTERY_CRITICAL: "battery",
    EventName.TASK_FAILED: "dispatcher",
    EventName.ESTOP_TRIGGERED: "system",
    EventName.ESTOP_RELEASED: "system",
    EventName.PATROL_CYCLE_FAILED: "patrol",
}
_DEFAULT_ALERT_SEVERITIES = {
    EventName.BATTERY_CRITICAL: "critical",
    EventName.TASK_FAILED: "error",
    EventName.ESTOP_TRIGGERED: "warning",
    EventName.ESTOP_RELEASED: "info",
    EventName.PATROL_CYCLE_FAILED: "error",
}
_EVENT_TYPE_MAP = {
    EventName.BATTERY_CRITICAL: "battery_critical",
    EventName.TASK_FAILED: "logistics_task_failed",
    EventName.ESTOP_TRIGGERED: "estop_triggered",
    EventName.ESTOP_RELEASED: "estop_released",
    EventName.PATROL_CYCLE_FAILED: "patrol_cycle_failed",
}
_OPTIONAL_ID_FIELDS = ("robot_id", "task_id", "cycle_id", "route_id", "job_id")
_GLOBAL_ROUTER_LOCK = threading.Lock()
_alert_router: AlertRouter | None = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _normalize_optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_non_empty_string(value, field_name)


def _normalize_timestamp(value: object) -> str:
    if value in {None, ""}:
        return _utc_now_iso()
    timestamp = _require_non_empty_string(value, "timestamp")
    normalized = timestamp.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("timestamp must be a valid ISO-8601 string") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_safe(item) for item in value)
    return str(value)


def _sanitize_metadata(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("metadata must be a dictionary")
    safe_value = redact_sensitive(_json_safe(value))
    json.dumps(safe_value)
    return dict(safe_value)


def _normalize_severity(value: object) -> str:
    severity = _require_non_empty_string(value, "severity").lower()
    if severity not in _VALID_SEVERITIES:
        raise ValueError("severity must be one of: info, warning, error, critical")
    return severity


def _source_from_text(*values: object) -> str:
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower()
        if not normalized:
            continue
        if "watchdog" in normalized:
            return "watchdog"
        if "battery" in normalized:
            return "battery"
        if "provision" in normalized:
            return "provisioning"
        if "dispatch" in normalized:
            return "dispatcher"
        if "patrol" in normalized:
            return "patrol"
    return "system"


@dataclass
class Alert:
    alert_id: str
    timestamp: str
    severity: str
    source: str
    event_type: str
    message: str
    robot_id: str | None = None
    task_id: str | None = None
    cycle_id: str | None = None
    route_id: str | None = None
    job_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    acknowledged: bool = False
    acknowledged_at: str | None = None
    acknowledged_by: str | None = None

    def __post_init__(self) -> None:
        self.alert_id = _require_non_empty_string(self.alert_id, "alert_id")
        self.timestamp = _normalize_timestamp(self.timestamp)
        self.severity = _normalize_severity(self.severity)
        self.source = _require_non_empty_string(self.source, "source")
        self.event_type = _require_non_empty_string(self.event_type, "event_type")
        self.message = _require_non_empty_string(self.message, "message")
        for field_name in _OPTIONAL_ID_FIELDS:
            setattr(self, field_name, _normalize_optional_string(getattr(self, field_name), field_name))
        self.metadata = _sanitize_metadata(self.metadata)
        self.acknowledged = bool(self.acknowledged)
        self.acknowledged_at = _normalize_optional_string(self.acknowledged_at, "acknowledged_at")
        self.acknowledged_by = _normalize_optional_string(self.acknowledged_by, "acknowledged_by")
        if self.acknowledged and self.acknowledged_at is None:
            self.acknowledged_at = _utc_now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "timestamp": self.timestamp,
            "severity": self.severity,
            "source": self.source,
            "event_type": self.event_type,
            "message": self.message,
            "robot_id": self.robot_id,
            "task_id": self.task_id,
            "cycle_id": self.cycle_id,
            "route_id": self.route_id,
            "job_id": self.job_id,
            "metadata": dict(self.metadata),
            "acknowledged": self.acknowledged,
            "acknowledged_at": self.acknowledged_at,
            "acknowledged_by": self.acknowledged_by,
        }


class AlertRouter:
    def __init__(
        self,
        *,
        max_alerts: int = _DEFAULT_MAX_ALERTS,
        event_bus: EventBus | None = None,
        ws_broker: WebSocketBroker | None = None,
    ) -> None:
        normalized_max_alerts = int(max_alerts)
        if normalized_max_alerts < 1:
            raise ValueError("max_alerts must be >= 1")
        self._max_alerts = normalized_max_alerts
        self._event_bus = event_bus or get_event_bus()
        self._ws_broker = ws_broker or get_ws_broker()
        self._alerts: list[Alert] = []
        self._subscription_ids: list[str] = []
        self._running = False
        self._lock = threading.RLock()

    async def start(self) -> None:
        if self._running:
            return
        if self._subscription_ids:
            self._running = True
            return
        self._subscription_ids = [
            self._event_bus.subscribe(event_name, self._handle_event, subscriber_name="observability_alert_router")
            for event_name in _ALERT_SUBSCRIPTIONS
        ]
        self._running = True

    async def stop(self) -> None:
        if not self._subscription_ids and not self._running:
            return
        for subscription_id in list(self._subscription_ids):
            self._event_bus.unsubscribe(subscription_id)
        self._subscription_ids.clear()
        self._running = False

    def emit(self, alert: Alert) -> Alert:
        return self._store_alert(alert, create_audit=True, notify_ws=True)

    def list_alerts(
        self,
        severity: str | None = None,
        robot_id: str | None = None,
        acknowledged: bool | None = None,
        limit: int = 100,
    ) -> list[Alert]:
        normalized_severity = severity.strip().lower() if isinstance(severity, str) and severity.strip() else None
        normalized_robot_id = robot_id.strip() if isinstance(robot_id, str) and robot_id.strip() else None
        normalized_limit = max(1, int(limit))
        with self._lock:
            alerts = list(reversed(self._alerts))
        filtered: list[Alert] = []
        for alert in alerts:
            if normalized_severity is not None and alert.severity != normalized_severity:
                continue
            if normalized_robot_id is not None and alert.robot_id != normalized_robot_id:
                continue
            if acknowledged is not None and alert.acknowledged is not bool(acknowledged):
                continue
            filtered.append(alert)
            if len(filtered) >= normalized_limit:
                break
        return filtered

    def get(self, alert_id: str) -> Alert | None:
        normalized_alert_id = _require_non_empty_string(alert_id, "alert_id")
        with self._lock:
            for index in range(len(self._alerts) - 1, -1, -1):
                if self._alerts[index].alert_id == normalized_alert_id:
                    return self._alerts[index]
        return None

    def acknowledge(self, alert_id: str, actor_id: str | None = None) -> Alert:
        normalized_alert_id = _require_non_empty_string(alert_id, "alert_id")
        normalized_actor_id = _normalize_optional_string(actor_id, "actor_id")
        with self._lock:
            for index, alert in enumerate(self._alerts):
                if alert.alert_id != normalized_alert_id:
                    continue
                if alert.acknowledged:
                    return alert
                updated = replace(
                    alert,
                    acknowledged=True,
                    acknowledged_at=_utc_now_iso(),
                    acknowledged_by=normalized_actor_id,
                )
                self._alerts[index] = updated
                return updated
        raise LookupError(f"Unknown alert_id: {normalized_alert_id}")

    async def _handle_event(self, event: Event) -> None:
        alert = self._alert_from_event(event)
        if alert is None:
            return
        try:
            self._store_alert(alert, create_audit=True, notify_ws=False)
        except Exception:
            logger.exception(
                "Alert routing failed",
                extra={"event_name": event.name.value, "event_id": event.event_id, "source": event.source},
            )

    def _store_alert(self, alert: Alert, *, create_audit: bool, notify_ws: bool) -> Alert:
        if not isinstance(alert, Alert):
            raise TypeError("alert must be an Alert")
        with self._lock:
            self._alerts.append(alert)
            if len(self._alerts) > self._max_alerts:
                self._alerts = self._alerts[-self._max_alerts :]
        if create_audit:
            self._write_audit_event(alert)
        if notify_ws:
            self._schedule_broadcast(alert)
        return alert

    def _write_audit_event(self, alert: Alert) -> None:
        if alert.severity not in {"warning", "error", "critical"}:
            return
        try:
            audit_event(
                event_type="alert_emitted",
                severity=alert.severity,
                actor_type="system",
                robot_id=alert.robot_id,
                task_id=alert.task_id,
                cycle_id=alert.cycle_id,
                route_id=alert.route_id,
                job_id=alert.job_id,
                message=alert.message,
                metadata={
                    "alert_id": alert.alert_id,
                    "alert_source": alert.source,
                    "alert_event_type": alert.event_type,
                    **alert.metadata,
                },
            )
        except Exception:
            logger.exception(
                "Alert audit write failed",
                extra={"alert_id": alert.alert_id, "severity": alert.severity, "event_type": alert.event_type},
            )

    def _schedule_broadcast(self, alert: Alert) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._broadcast_alert(alert))

    async def _broadcast_alert(self, alert: Alert) -> None:
        try:
            await self._ws_broker.broadcast({"type": "alert", "alert": alert.to_dict()})
        except Exception:
            logger.warning(
                "Alert broadcast failed",
                extra={"alert_id": alert.alert_id, "source": alert.source, "event_type": alert.event_type},
            )

    def _alert_from_event(self, event: Event) -> Alert | None:
        if event.name is EventName.SYSTEM_ALERT:
            return self._alert_from_system_event(event)
        if event.name is EventName.BATTERY_CRITICAL:
            return self._alert_from_battery_event(event)
        if event.name is EventName.TASK_FAILED:
            return self._alert_from_task_failed_event(event)
        if event.name in {EventName.ESTOP_TRIGGERED, EventName.ESTOP_RELEASED}:
            return self._alert_from_estop_event(event)
        if event.name is EventName.PATROL_CYCLE_FAILED:
            return self._alert_from_patrol_failure_event(event)
        return None

    def _alert_from_system_event(self, event: Event) -> Alert:
        payload = dict(event.payload or {})
        reason = payload.pop("reason", None)
        event_type = _require_non_empty_string(reason or "system_alert", "event_type")
        raw_message = payload.pop("message", None)
        message = raw_message.strip() if isinstance(raw_message, str) and raw_message.strip() else event_type.replace("_", " ")
        task_id = payload.pop("task_id", None) or payload.pop("active_task_id", None) or event.task_id
        cycle_id = payload.pop("cycle_id", None)
        route_id = payload.pop("route_id", None)
        job_id = payload.pop("job_id", None)
        robot_id = payload.pop("robot_id", None)
        module_name = payload.pop("module", None)
        return Alert(
            alert_id=event.event_id,
            timestamp=event.timestamp.isoformat().replace("+00:00", "Z"),
            severity=payload.pop("severity", "warning"),
            source=_source_from_text(module_name, event.source),
            event_type=event_type,
            message=message,
            robot_id=robot_id,
            task_id=task_id,
            cycle_id=cycle_id,
            route_id=route_id,
            job_id=job_id,
            metadata=payload,
        )

    def _alert_from_battery_event(self, event: Event) -> Alert:
        payload = dict(event.payload or {})
        robot_id = payload.pop("robot_id", None)
        return Alert(
            alert_id=event.event_id,
            timestamp=event.timestamp.isoformat().replace("+00:00", "Z"),
            severity="critical",
            source="battery",
            event_type="battery_critical",
            message="Battery level is critical",
            robot_id=robot_id,
            task_id=event.task_id,
            metadata=payload,
        )

    def _alert_from_task_failed_event(self, event: Event) -> Alert:
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

    def _alert_from_estop_event(self, event: Event) -> Alert:
        payload = dict(event.payload or {})
        event_type = _EVENT_TYPE_MAP[event.name]
        severity = _DEFAULT_ALERT_SEVERITIES[event.name]
        source = _source_from_text(payload.get("source"), event.source, _DEFAULT_ALERT_SOURCES[event.name])
        message = _DEFAULT_ALERT_MESSAGES[event.name]
        reason = payload.get("reason")
        if isinstance(reason, str) and reason.strip() and event.name is EventName.ESTOP_TRIGGERED:
            message = f"Emergency stop triggered: {reason.strip()}"
        return Alert(
            alert_id=event.event_id,
            timestamp=event.timestamp.isoformat().replace("+00:00", "Z"),
            severity=severity,
            source=source,
            event_type=event_type,
            message=message,
            robot_id=payload.pop("robot_id", None),
            task_id=payload.pop("task_id", None) or event.task_id,
            cycle_id=payload.pop("cycle_id", None),
            route_id=payload.pop("route_id", None),
            job_id=payload.pop("job_id", None),
            metadata=payload,
        )

    def _alert_from_patrol_failure_event(self, event: Event) -> Alert:
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


def get_alert_router() -> AlertRouter:
    global _alert_router
    if _alert_router is not None:
        return _alert_router
    with _GLOBAL_ROUTER_LOCK:
        if _alert_router is None:
            _alert_router = AlertRouter()
    return _alert_router


def emit_alert(
    severity: str,
    source: str,
    event_type: str,
    message: str,
    robot_id: str | None = None,
    task_id: str | None = None,
    cycle_id: str | None = None,
    route_id: str | None = None,
    job_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Alert | None:
    try:
        alert = Alert(
            alert_id=str(uuid4()),
            timestamp=_utc_now_iso(),
            severity=severity,
            source=source,
            event_type=event_type,
            message=message,
            robot_id=robot_id,
            task_id=task_id,
            cycle_id=cycle_id,
            route_id=route_id,
            job_id=job_id,
            metadata=metadata or {},
        )
        return get_alert_router().emit(alert)
    except Exception:
        logger.exception(
            "Alert emit failed",
            extra={
                "severity": severity,
                "source": source,
                "event_type": event_type,
                "robot_id": robot_id,
                "task_id": task_id,
                "cycle_id": cycle_id,
                "route_id": route_id,
                "job_id": job_id,
            },
        )
        return None


__all__ = [
    "Alert",
    "AlertRouter",
    "MASKED_VALUE",
    "emit_alert",
    "get_alert_router",
]
