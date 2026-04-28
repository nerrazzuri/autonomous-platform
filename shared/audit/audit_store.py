from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shared.audit.audit_models import AuditEvent
from shared.core.logger import get_logger


logger = get_logger(__name__)
DEFAULT_AUDIT_PATH = Path("data/audit/audit_events.jsonl")
_DEFAULT_AUDIT_STORE: AuditStore | None = None


class AuditStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_AUDIT_PATH

    def append(self, event: AuditEvent) -> AuditEvent:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=True, default=str))
            handle.write("\n")
        return event

    def get(self, event_id: str) -> AuditEvent | None:
        for event in self._load_events():
            if event.event_id == event_id:
                return event
        return None

    def list_events(
        self,
        robot_id: str | None = None,
        event_type: str | None = None,
        severity: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        normalized_limit = max(1, int(limit))
        filtered: list[AuditEvent] = []
        for event in reversed(self._load_events()):
            if robot_id is not None and event.robot_id != robot_id:
                continue
            if event_type is not None and event.event_type != event_type:
                continue
            if severity is not None and event.severity != severity.lower():
                continue
            filtered.append(event)
            if len(filtered) >= normalized_limit:
                break
        return filtered

    def _load_events(self) -> list[AuditEvent]:
        if not self.path.exists():
            return []

        events: list[AuditEvent] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("audit event line must be a JSON object")
                events.append(AuditEvent.from_dict(payload))
        return events


def get_audit_store(path: str | Path | None = None) -> AuditStore:
    global _DEFAULT_AUDIT_STORE

    if path is not None:
        return AuditStore(path)
    if _DEFAULT_AUDIT_STORE is None:
        _DEFAULT_AUDIT_STORE = AuditStore()
    return _DEFAULT_AUDIT_STORE


def audit_event(
    event_type: str,
    severity: str = "info",
    actor_type: str = "system",
    actor_id: str | None = None,
    robot_id: str | None = None,
    task_id: str | None = None,
    cycle_id: str | None = None,
    route_id: str | None = None,
    job_id: str | None = None,
    message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent | None:
    try:
        event = AuditEvent(
            event_type=event_type,
            severity=severity,
            actor_type=actor_type,
            actor_id=actor_id,
            robot_id=robot_id,
            task_id=task_id,
            cycle_id=cycle_id,
            route_id=route_id,
            job_id=job_id,
            message=message,
            metadata=metadata or {},
        )
        return get_audit_store().append(event)
    except Exception:
        logger.exception(
            "Audit event append failed",
            extra={
                "event_type": event_type,
                "robot_id": robot_id,
                "task_id": task_id,
                "cycle_id": cycle_id,
                "route_id": route_id,
                "job_id": job_id,
            },
        )
        return None
