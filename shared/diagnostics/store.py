"""Thread-safe in-memory diagnostic event ring buffer."""

from __future__ import annotations

from collections import deque
from threading import RLock
from typing import Any

from shared.diagnostics.events import DiagnosticEvent, DiagnosticSeverity, normalize_severity


class DiagnosticEventStore:
    def __init__(self, max_events: int = 1000):
        if type(max_events) is not int or max_events <= 0:
            raise ValueError("max_events must be a positive integer")
        self._events: deque[DiagnosticEvent] = deque(maxlen=max_events)
        self._lock = RLock()

    def add(self, event: DiagnosticEvent) -> DiagnosticEvent:
        if not isinstance(event, DiagnosticEvent):
            raise ValueError("event must be a DiagnosticEvent")
        with self._lock:
            self._events.append(event)
        return event

    def create_event(self, **kwargs) -> DiagnosticEvent:
        return self.add(DiagnosticEvent.create(**kwargs))

    def recent(
        self,
        limit: int = 100,
        severity: DiagnosticSeverity | str | None = None,
        module: str | None = None,
        error_code: str | None = None,
        robot_id: str | None = None,
        task_id: str | None = None,
    ) -> list[DiagnosticEvent]:
        if limit <= 0:
            return []
        normalized_severity = normalize_severity(severity) if severity is not None else None
        with self._lock:
            events = list(reversed(self._events))

        filtered: list[DiagnosticEvent] = []
        for event in events:
            if normalized_severity is not None and event.severity != normalized_severity:
                continue
            if module is not None and event.module != module:
                continue
            if error_code is not None and event.error_code != error_code:
                continue
            if robot_id is not None and event.robot_id != robot_id:
                continue
            if task_id is not None and event.task_id != task_id:
                continue
            filtered.append(event)
            if len(filtered) >= limit:
                break
        return filtered

    def errors(self, limit: int = 100) -> list[DiagnosticEvent]:
        if limit <= 0:
            return []
        with self._lock:
            events = list(reversed(self._events))
        results = [
            event
            for event in events
            if event.severity in {DiagnosticSeverity.ERROR, DiagnosticSeverity.CRITICAL}
        ]
        return results[:limit]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def count(self) -> int:
        with self._lock:
            return len(self._events)

    def to_list(self, limit: int | None = None) -> list[dict[str, Any]]:
        events = self.recent(limit=limit if limit is not None else self.count())
        return [event.to_dict() for event in events]


_default_store = DiagnosticEventStore()


def get_diagnostic_store() -> DiagnosticEventStore:
    return _default_store


def reset_diagnostic_store(max_events: int = 1000) -> DiagnosticEventStore:
    global _default_store
    _default_store = DiagnosticEventStore(max_events=max_events)
    return _default_store
