"""Diagnostic event model and JSON serialization helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any
from uuid import uuid4

from shared.diagnostics.error_codes import get_suggested_action
from shared.diagnostics.redaction import redact_mapping

ContextValue = str | int | float | bool | None


class DiagnosticSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


def normalize_severity(value: DiagnosticSeverity | str) -> DiagnosticSeverity:
    if isinstance(value, DiagnosticSeverity):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        for severity in DiagnosticSeverity:
            if severity.value == normalized:
                return severity
    raise ValueError(f"Unknown diagnostic severity: {value}")


def _require_non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _normalize_optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_non_empty_string(value, field_name)


def _normalize_context(value: Mapping[str, Any] | None) -> dict[str, ContextValue]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("context must be a mapping")
    redacted = redact_mapping(value)
    normalized: dict[str, ContextValue] = {}
    for key, item in redacted.items():
        context_key = _require_non_empty_string(str(key), "context key")
        if item is None or isinstance(item, (str, int, float, bool)):
            normalized[context_key] = item
        else:
            normalized[context_key] = json.dumps(item, default=repr, sort_keys=True)
    return normalized


@dataclass(frozen=True)
class DiagnosticEvent:
    event_id: str
    ts: str
    severity: DiagnosticSeverity
    module: str
    event: str
    message: str
    error_code: str | None = None
    subsystem: str | None = None
    robot_id: str | None = None
    context: dict[str, ContextValue] = field(default_factory=dict)
    # Deprecated compatibility fields. App/domain-specific identifiers should
    # be passed through context; these remain temporarily for existing callers.
    task_id: str | None = None
    route_id: str | None = None
    station_id: str | None = None
    waypoint_id: str | None = None
    correlation_id: str | None = None
    source: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    suggested_action: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty_string(self.event_id, "event_id"))
        object.__setattr__(self, "ts", _require_non_empty_string(self.ts, "ts"))
        object.__setattr__(self, "severity", normalize_severity(self.severity))
        object.__setattr__(self, "module", _require_non_empty_string(self.module, "module"))
        object.__setattr__(self, "event", _require_non_empty_string(self.event, "event"))
        object.__setattr__(self, "message", _require_non_empty_string(self.message, "message"))

        for field_name in (
            "error_code",
            "subsystem",
            "robot_id",
            "task_id",
            "route_id",
            "station_id",
            "waypoint_id",
            "correlation_id",
            "source",
            "suggested_action",
        ):
            object.__setattr__(self, field_name, _normalize_optional_string(getattr(self, field_name), field_name))

        if not isinstance(self.details, Mapping):
            raise ValueError("details must be a mapping")
        object.__setattr__(self, "details", redact_mapping(self.details))
        context = _normalize_context(self.context)
        for field_name in ("task_id", "route_id", "station_id", "waypoint_id"):
            legacy_value = getattr(self, field_name)
            if legacy_value is not None and field_name not in context:
                context[field_name] = legacy_value
        object.__setattr__(self, "context", context)

        if self.suggested_action is None and self.error_code is not None:
            object.__setattr__(self, "suggested_action", get_suggested_action(self.error_code))

    @classmethod
    def create(
        cls,
        *,
        severity: DiagnosticSeverity | str,
        module: str,
        event: str,
        message: str,
        error_code: str | None = None,
        subsystem: str | None = None,
        robot_id: str | None = None,
        context: Mapping[str, Any] | None = None,
        task_id: str | None = None,
        route_id: str | None = None,
        station_id: str | None = None,
        waypoint_id: str | None = None,
        correlation_id: str | None = None,
        source: str | None = None,
        details: Mapping[str, Any] | None = None,
        suggested_action: str | None = None,
    ) -> "DiagnosticEvent":
        return cls(
            event_id=str(uuid4()),
            ts=datetime.now(timezone.utc).isoformat(),
            severity=severity,
            module=module,
            event=event,
            message=message,
            error_code=error_code,
            subsystem=subsystem,
            robot_id=robot_id,
            context=dict(context or {}),
            task_id=task_id,
            route_id=route_id,
            station_id=station_id,
            waypoint_id=waypoint_id,
            correlation_id=correlation_id,
            source=source,
            details=dict(details or {}),
            suggested_action=suggested_action,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "ts": self.ts,
            "severity": self.severity.value,
            "module": self.module,
            "event": self.event,
            "message": self.message,
            "error_code": self.error_code,
            "subsystem": self.subsystem,
            "robot_id": self.robot_id,
            "context": dict(self.context),
            "task_id": self.task_id,
            "route_id": self.route_id,
            "station_id": self.station_id,
            "waypoint_id": self.waypoint_id,
            "correlation_id": self.correlation_id,
            "source": self.source,
            "details": dict(self.details),
            "suggested_action": self.suggested_action,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DiagnosticEvent":
        if not isinstance(data, Mapping):
            raise ValueError("diagnostic event data must be a mapping")
        return cls(
            event_id=data.get("event_id"),
            ts=data.get("ts"),
            severity=data.get("severity"),
            module=data.get("module"),
            event=data.get("event"),
            message=data.get("message"),
            error_code=data.get("error_code"),
            subsystem=data.get("subsystem"),
            robot_id=data.get("robot_id"),
            context=data.get("context") or {},
            task_id=data.get("task_id"),
            route_id=data.get("route_id"),
            station_id=data.get("station_id"),
            waypoint_id=data.get("waypoint_id"),
            correlation_id=data.get("correlation_id"),
            source=data.get("source"),
            details=data.get("details") or {},
            suggested_action=data.get("suggested_action"),
        )

    @classmethod
    def from_json(cls, payload: str) -> "DiagnosticEvent":
        return cls.from_dict(json.loads(payload))
