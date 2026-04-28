from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from typing import Any
from uuid import uuid4

from shared.core.logger import redact_sensitive


_VALID_SEVERITIES = {"debug", "info", "warning", "error", "critical"}
_VALID_ACTOR_TYPES = {"system", "operator", "api", "unknown"}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


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
    safe_metadata = redact_sensitive(_json_safe(value))
    json.dumps(safe_metadata)
    return safe_metadata


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=_utc_now_iso)
    severity: str = "info"
    actor_type: str = "system"
    actor_id: str | None = None
    robot_id: str | None = None
    task_id: str | None = None
    cycle_id: str | None = None
    route_id: str | None = None
    job_id: str | None = None
    message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", _require_non_empty_string(self.event_type, "event_type"))
        object.__setattr__(self, "event_id", _require_non_empty_string(self.event_id, "event_id"))
        object.__setattr__(self, "timestamp", _normalize_timestamp(self.timestamp))

        normalized_severity = _require_non_empty_string(self.severity, "severity").lower()
        if normalized_severity not in _VALID_SEVERITIES:
            raise ValueError("severity must be one of: debug, info, warning, error, critical")
        object.__setattr__(self, "severity", normalized_severity)

        normalized_actor_type = _require_non_empty_string(self.actor_type, "actor_type").lower()
        if normalized_actor_type not in _VALID_ACTOR_TYPES:
            raise ValueError("actor_type must be one of: system, operator, api, unknown")
        object.__setattr__(self, "actor_type", normalized_actor_type)

        for field_name in ("actor_id", "robot_id", "task_id", "cycle_id", "route_id", "job_id", "message"):
            object.__setattr__(self, field_name, _normalize_optional_string(getattr(self, field_name), field_name))

        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "severity": self.severity,
            "actor_type": self.actor_type,
            "actor_id": self.actor_id,
            "robot_id": self.robot_id,
            "task_id": self.task_id,
            "cycle_id": self.cycle_id,
            "route_id": self.route_id,
            "job_id": self.job_id,
            "message": self.message,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AuditEvent":
        return cls(
            event_id=payload.get("event_id") or str(uuid4()),
            timestamp=payload.get("timestamp") or _utc_now_iso(),
            event_type=payload.get("event_type", ""),
            severity=payload.get("severity", "info"),
            actor_type=payload.get("actor_type", "system"),
            actor_id=payload.get("actor_id"),
            robot_id=payload.get("robot_id"),
            task_id=payload.get("task_id"),
            cycle_id=payload.get("cycle_id"),
            route_id=payload.get("route_id"),
            job_id=payload.get("job_id"),
            message=payload.get("message"),
            metadata=payload.get("metadata") or {},
        )
