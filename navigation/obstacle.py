from __future__ import annotations

"""Phase 1 null obstacle detector contract for navigation."""

import asyncio
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.logger import get_logger


logger = get_logger(__name__)


class ObstacleDetectorError(Exception):
    """Raised when obstacle detector configuration or status data is invalid."""


@dataclass(frozen=True)
class ObstacleStatus:
    obstacle_present: bool
    source: str = "null_detector"
    confidence: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ObstacleDetectorError("source must not be empty")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(self.confidence)
        ):
            raise ObstacleDetectorError("confidence must be a finite number")
        if self.confidence < 0.0 or self.confidence > 1.0:
            raise ObstacleDetectorError("confidence must be between 0.0 and 1.0")
        if self.timestamp.tzinfo is None:
            raise ObstacleDetectorError("timestamp must be timezone-aware")
        if not isinstance(self.metadata, dict):
            raise ObstacleDetectorError("metadata must be a dictionary")

        object.__setattr__(self, "source", self.source.strip())
        object.__setattr__(self, "confidence", float(self.confidence))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "obstacle_present": self.obstacle_present,
            "source": self.source,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def clear(cls, source: str = "null_detector") -> "ObstacleStatus":
        return cls(obstacle_present=False, source=source, confidence=0.0, metadata={})

    @classmethod
    def detected(
        cls,
        source: str,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> "ObstacleStatus":
        return cls(
            obstacle_present=True,
            source=source,
            confidence=confidence,
            metadata=metadata or {},
        )


class ObstacleDetector:
    """Safe null obstacle detector with an async lifecycle for future real detectors."""

    def __init__(self, polling_interval_seconds: float = 0.2, enabled: bool = True) -> None:
        if (
            isinstance(polling_interval_seconds, bool)
            or not isinstance(polling_interval_seconds, (int, float))
            or not math.isfinite(polling_interval_seconds)
            or polling_interval_seconds <= 0
        ):
            raise ObstacleDetectorError("polling_interval_seconds must be > 0")
        self._polling_interval_seconds = float(polling_interval_seconds)
        self._enabled = enabled
        self._last_status = ObstacleStatus.clear()
        self._status_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._poll_count = 0
        self._last_error: str | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return

        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="sumitomo-obstacle-detector")
        logger.info("Obstacle detector started", extra={"enabled": self._enabled})

    async def stop(self) -> None:
        if self._task is None:
            return
        if self._task.done():
            self._task = None
            return

        self._stop_event.set()
        try:
            await self._task
        finally:
            self._task = None
            logger.info("Obstacle detector stopped")

    async def poll_once(self) -> ObstacleStatus:
        status = await self._detect_obstacle()
        async with self._status_lock:
            self._last_status = status
        self._poll_count += 1
        logger.debug("Obstacle detector poll completed", extra={"obstacle_present": status.obstacle_present})
        return status

    async def get_status(self) -> ObstacleStatus:
        async with self._status_lock:
            return self._last_status

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def poll_count(self) -> int:
        return self._poll_count

    def last_error(self) -> str | None:
        return self._last_error

    async def _detect_obstacle(self) -> ObstacleStatus:
        return ObstacleStatus.clear()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.poll_once()
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception("Obstacle detector poll failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._polling_interval_seconds)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break


obstacle_detector = ObstacleDetector()


def get_obstacle_detector() -> ObstacleDetector:
    return obstacle_detector


__all__ = [
    "ObstacleDetector",
    "ObstacleDetectorError",
    "ObstacleStatus",
    "get_obstacle_detector",
    "obstacle_detector",
]
