from __future__ import annotations

"""Safe Phase 1 GPIO relay contract for future station lights and buzzers."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from shared.core.config import get_config
from shared.core.logger import get_logger


logger = get_logger(__name__)

_SUPPORTED_LEVELS = {"info", "warning", "critical"}
_SUPPORTED_ACTIONS = {"trigger", "clear"}


class GPIORelayError(Exception):
    """Raised when GPIO relay inputs are invalid or unsafe to process."""


def _normalize_station_id(station_id: str) -> str:
    if not isinstance(station_id, str) or not station_id.strip():
        raise GPIORelayError("station_id must not be empty")
    return station_id.strip()


def _normalize_level(level: str) -> str:
    normalized = level.strip().lower() if isinstance(level, str) else ""
    if normalized not in _SUPPORTED_LEVELS:
        allowed = ", ".join(sorted(_SUPPORTED_LEVELS))
        raise GPIORelayError(f"level must be one of: {allowed}")
    return normalized


def _normalize_action(action: str) -> str:
    normalized = action.strip().lower() if isinstance(action, str) else ""
    if normalized not in _SUPPORTED_ACTIONS:
        allowed = ", ".join(sorted(_SUPPORTED_ACTIONS))
        raise GPIORelayError(f"action must be one of: {allowed}")
    return normalized


def _normalize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise GPIORelayError("metadata must be a dictionary when provided")
    return dict(metadata)


@dataclass(frozen=True)
class RelayEvent:
    station_id: str
    level: str
    action: str
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "station_id", _normalize_station_id(self.station_id))
        object.__setattr__(self, "level", _normalize_level(self.level))
        object.__setattr__(self, "action", _normalize_action(self.action))
        if not isinstance(self.timestamp, datetime):
            raise GPIORelayError("timestamp must be a datetime")
        object.__setattr__(self, "metadata", _normalize_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "station_id": self.station_id,
            "level": self.level,
            "action": self.action,
            "timestamp": self.timestamp.isoformat(),
            "metadata": dict(self.metadata),
        }


class GPIORelay:
    """Phase 1 in-memory relay stub with logging and input validation only."""

    def __init__(self, enabled: bool | None = None) -> None:
        self._enabled = self._resolve_enabled(enabled)
        self._events: list[RelayEvent] = []

    async def trigger_alert(
        self,
        station_id: str,
        level: str = "warning",
        metadata: dict[str, Any] | None = None,
    ) -> RelayEvent:
        event = RelayEvent(
            station_id=_normalize_station_id(station_id),
            level=_normalize_level(level),
            action="trigger",
            timestamp=datetime.now(timezone.utc),
            metadata=_normalize_metadata(metadata),
        )
        self._events.append(event)
        logger.info(
            "GPIO relay alert triggered (Phase 1 no-op)",
            extra={
                "station_id": event.station_id,
                "level": event.level,
                "action": event.action,
                "enabled": self._enabled,
                "metadata": event.metadata,
            },
        )
        return event

    async def clear_alert(self, station_id: str, metadata: dict[str, Any] | None = None) -> RelayEvent:
        event = RelayEvent(
            station_id=_normalize_station_id(station_id),
            level="info",
            action="clear",
            timestamp=datetime.now(timezone.utc),
            metadata=_normalize_metadata(metadata),
        )
        self._events.append(event)
        logger.info(
            "GPIO relay alert cleared (Phase 1 no-op)",
            extra={
                "station_id": event.station_id,
                "level": event.level,
                "action": event.action,
                "enabled": self._enabled,
                "metadata": event.metadata,
            },
        )
        return event

    async def get_last_event(self, station_id: str) -> RelayEvent | None:
        normalized_station_id = _normalize_station_id(station_id)
        for event in reversed(self._events):
          if event.station_id == normalized_station_id:
              return event
        return None

    async def list_events(self) -> list[RelayEvent]:
        return list(self._events)

    def is_enabled(self) -> bool:
        return self._enabled

    def _resolve_enabled(self, enabled: bool | None) -> bool:
        if enabled is not None:
            return bool(enabled)
        try:
            return bool(get_config().alerts.gpio_alert_enabled)
        except Exception:
            logger.warning("GPIO relay config unavailable, defaulting disabled")
            return False


gpio_relay = GPIORelay()


def get_gpio_relay() -> GPIORelay:
    return gpio_relay


__all__ = [
    "GPIORelay",
    "GPIORelayError",
    "RelayEvent",
    "get_gpio_relay",
    "gpio_relay",
]
