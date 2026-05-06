from __future__ import annotations

"""Phase 1 null obstacle detector contract for navigation."""

import asyncio
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from shared.core.event_bus import EventName, get_event_bus
from shared.core.logger import get_logger
from shared.diagnostics import DiagnosticReporter, error_codes, get_diagnostic_reporter


logger = get_logger(__name__)


def _check_forward_arc(scan: Any, stop_distance_m: float, arc_half_deg: float) -> bool:
    arc_half_rad = math.radians(arc_half_deg)
    range_min = float(getattr(scan, "range_min", 0.0))
    range_max_raw = getattr(scan, "range_max", None)
    range_max: float | None = (
        float(range_max_raw) if range_max_raw is not None and float(range_max_raw) > 0 else None
    )
    angle_min = float(scan.angle_min)
    angle_increment = float(scan.angle_increment)
    for i, r in enumerate(scan.ranges):
        if not math.isfinite(r) or r <= 0:
            continue
        if r < range_min:
            continue
        if range_max is not None and r > range_max:
            continue
        angle = angle_min + i * angle_increment
        if abs(angle) <= arc_half_rad and r <= stop_distance_m:
            return True
    return False


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

    def __init__(
        self,
        polling_interval_seconds: float = 0.2,
        enabled: bool = True,
        stop_distance_m: float = 0.8,
        forward_arc_deg: float = 90.0,
        reporter: DiagnosticReporter | None = None,
    ) -> None:
        if (
            isinstance(polling_interval_seconds, bool)
            or not isinstance(polling_interval_seconds, (int, float))
            or not math.isfinite(polling_interval_seconds)
            or polling_interval_seconds <= 0
        ):
            raise ObstacleDetectorError("polling_interval_seconds must be > 0")
        self._polling_interval_seconds = float(polling_interval_seconds)
        self._enabled = enabled
        self._stop_distance_m = float(stop_distance_m)
        self._arc_half_deg = float(forward_arc_deg) / 2.0
        self._last_status = ObstacleStatus.clear()
        self._status_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._poll_count = 0
        self._last_error: str | None = None
        self._diagnostic_reporter = reporter
        self._invalid_scan_reported = False
        self._poll_failure_reported = False

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return

        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="platform-obstacle-detector")
        self._report_diagnostic("info", event="obstacle_detector.started", message="Obstacle detector started.")
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
            self._report_diagnostic("info", event="obstacle_detector.stopped", message="Obstacle detector stopped.")
            logger.info("Obstacle detector stopped")

    async def poll_once(self) -> ObstacleStatus:
        status = await self._detect_obstacle()
        async with self._status_lock:
            previous_status = self._last_status
            self._last_status = status
        self._poll_count += 1
        self._publish_transition(previous_status, status)
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
        try:
            from shared.ros2 import get_bridge
        except Exception:
            return ObstacleStatus.clear()

        bridge = get_bridge()
        if bridge is None:
            return ObstacleStatus.clear()

        scan = bridge.get_latest_scan()
        if scan is None:
            return ObstacleStatus.clear()

        try:
            if _check_forward_arc(scan, self._stop_distance_m, self._arc_half_deg):
                return ObstacleStatus.detected(source="m10_lidar", confidence=1.0)
            return ObstacleStatus.clear()
        except Exception as exc:
            if not self._invalid_scan_reported:
                self._invalid_scan_reported = True
                self._report_diagnostic(
                    "warning",
                    event="obstacle.scan_invalid",
                    message="Obstacle detector received an invalid scan message.",
                    error_code=error_codes.LIDAR_SCAN_TIMEOUT,
                    details={"error_type": type(exc).__name__},
                )
            logger.warning("Obstacle detector: invalid scan message", extra={"error": str(exc)})
            return ObstacleStatus.clear()

    def _publish_transition(self, previous_status: ObstacleStatus, current_status: ObstacleStatus) -> None:
        if previous_status.obstacle_present == current_status.obstacle_present:
            return

        event_name = (
            EventName.OBSTACLE_DETECTED
            if current_status.obstacle_present
            else EventName.OBSTACLE_CLEARED
        )
        try:
            get_event_bus().publish_nowait(event_name, payload=current_status.to_dict(), source=__name__)
        except asyncio.QueueFull:
            logger.warning("Obstacle detector event bus queue full", extra={"event_name": event_name.value})
        except Exception:
            logger.exception("Obstacle detector failed to publish event", extra={"event_name": event_name.value})
        if current_status.obstacle_present:
            self._report_diagnostic(
                "warning",
                event="obstacle.detected",
                message="Obstacle detected in safety path.",
                error_code=error_codes.OBSTACLE_DETECTED,
                details=current_status.to_dict(),
            )
        else:
            self._report_diagnostic(
                "info",
                event="obstacle.cleared",
                message="Obstacle cleared from safety path.",
                error_code=error_codes.OBSTACLE_CLEARED,
                details=current_status.to_dict(),
            )

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.poll_once()
                if self._poll_failure_reported:
                    self._poll_failure_reported = False
                    self._report_diagnostic(
                        "info",
                        event="obstacle_detector.recovered",
                        message="Obstacle detector polling recovered.",
                    )
            except Exception as exc:
                self._last_error = str(exc)
                if not self._poll_failure_reported:
                    self._poll_failure_reported = True
                    self._report_diagnostic(
                        "error",
                        event="obstacle_detector.poll_failed",
                        message="Obstacle detector polling failed.",
                        details={"error_type": type(exc).__name__},
                    )
                logger.exception("Obstacle detector poll failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._polling_interval_seconds)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def _report_diagnostic(
        self,
        severity: str,
        *,
        event: str,
        message: str,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        try:
            reporter = self._diagnostic_reporter or get_diagnostic_reporter("obstacle")
            reporter.report(
                severity=severity,
                event=event,
                message=message,
                error_code=error_code,
                subsystem="obstacle",
                source=__name__,
                details=details,
            )
        except Exception:
            logger.debug("Obstacle diagnostic reporting failed", exc_info=True)


obstacle_detector = ObstacleDetector()


def get_obstacle_detector() -> ObstacleDetector:
    return obstacle_detector


__all__ = [
    "ObstacleDetector",
    "ObstacleDetectorError",
    "ObstacleStatus",
    "_check_forward_arc",
    "get_obstacle_detector",
    "obstacle_detector",
]
