from __future__ import annotations

"""Safe Phase 1 MES bridge contract for future external task submission."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from shared.core.logger import get_logger
from apps.logistics.tasks.queue import TaskQueue, get_task_queue


logger = get_logger(__name__)


class MESBridgeError(Exception):
    """Raised when MES payloads or bridge operations are invalid."""


def _normalize_required_text(field_name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MESBridgeError(f"{field_name} must not be empty")
    return value.strip()


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MESBridgeError("batch_id must be a string when provided")
    stripped = value.strip()
    return stripped if stripped else None


def _normalize_priority(value: Any) -> int:
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MESBridgeError("priority must be a non-negative integer")
    return value


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise MESBridgeError("payload must be a dictionary")
    return dict(payload)


@dataclass(frozen=True)
class MESEvent:
    event_id: str
    station_id: str
    destination_id: str
    batch_id: str | None
    priority: int
    timestamp: datetime
    raw_payload: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _normalize_required_text("event_id", self.event_id))
        object.__setattr__(self, "station_id", _normalize_required_text("station_id", self.station_id))
        object.__setattr__(self, "destination_id", _normalize_required_text("destination_id", self.destination_id))
        object.__setattr__(self, "batch_id", _normalize_optional_text(self.batch_id))
        object.__setattr__(self, "priority", _normalize_priority(self.priority))
        if not isinstance(self.timestamp, datetime):
            raise MESBridgeError("timestamp must be a datetime")
        object.__setattr__(self, "raw_payload", _normalize_payload(self.raw_payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "station_id": self.station_id,
            "destination_id": self.destination_id,
            "batch_id": self.batch_id,
            "priority": self.priority,
            "timestamp": self.timestamp.isoformat(),
            "raw_payload": dict(self.raw_payload),
        }


class MESBridge:
    """Phase 1 stub bridge that validates payloads and submits queue tasks only."""

    def __init__(self, task_queue: TaskQueue | None = None, enabled: bool = False) -> None:
        self._task_queue = task_queue or get_task_queue()
        self._enabled = bool(enabled)
        self._running = False
        self._submitted_count = 0
        self._last_error: str | None = None

    async def start_listener(self) -> None:
        if self._running:
            return
        self._running = True
        logger.info("MES bridge listener started (Phase 1 no-op)", extra={"enabled": self._enabled})

    async def stop_listener(self) -> None:
        if not self._running:
            return
        self._running = False
        logger.info("MES bridge listener stopped (Phase 1 no-op)", extra={"enabled": self._enabled})

    async def submit_mes_event(self, payload: dict[str, Any]) -> MESEvent:
        normalized_payload = _normalize_payload(payload)
        event = self._build_event(normalized_payload)
        try:
            await self._task_queue.submit_task(
                station_id=event.station_id,
                destination_id=event.destination_id,
                batch_id=event.batch_id,
                priority=event.priority,
                notes="Submitted by MES bridge",
            )
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning(
                "MES bridge task submission failed",
                extra={"event_id": event.event_id, "station_id": event.station_id, "error": self._last_error},
            )
            raise MESBridgeError(self._last_error) from exc

        self._submitted_count += 1
        self._last_error = None
        logger.info(
            "MES bridge event submitted",
            extra={
                "event_id": event.event_id,
                "station_id": event.station_id,
                "destination_id": event.destination_id,
                "priority": event.priority,
                "submitted_count": self._submitted_count,
            },
        )
        return event

    def is_running(self) -> bool:
        return self._running

    def is_enabled(self) -> bool:
        return self._enabled

    def submitted_count(self) -> int:
        return self._submitted_count

    def last_error(self) -> str | None:
        return self._last_error

    def _build_event(self, payload: dict[str, Any]) -> MESEvent:
        return MESEvent(
            event_id=_normalize_required_text("event_id", payload.get("event_id") or str(uuid4())),
            station_id=_normalize_required_text("station_id", payload.get("station_id")),
            destination_id=_normalize_required_text("destination_id", payload.get("destination_id", "QA")),
            batch_id=_normalize_optional_text(payload.get("batch_id")),
            priority=_normalize_priority(payload.get("priority", 0)),
            timestamp=datetime.now(timezone.utc),
            raw_payload=payload,
        )


mes_bridge = MESBridge()


def get_mes_bridge() -> MESBridge:
    return mes_bridge


__all__ = [
    "MESBridge",
    "MESBridgeError",
    "MESEvent",
    "get_mes_bridge",
    "mes_bridge",
]
