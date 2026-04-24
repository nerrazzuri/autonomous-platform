from __future__ import annotations

"""Phase 1 corrected-position provider with odometry fallback."""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.logger import get_logger
from quadruped.state_monitor import QuadrupedState, StateMonitor, get_state_monitor


logger = get_logger(__name__)


class SLAMProviderError(Exception):
    """Raised when corrected-position data cannot be produced safely."""


def _validate_finite_number(field_name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SLAMProviderError(f"{field_name} must be a finite number")
    return float(value)


@dataclass(frozen=True)
class CorrectedPosition:
    x: float
    y: float
    heading_rad: float
    source: str = "odometry_fallback"
    confidence: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _validate_finite_number("x", self.x))
        object.__setattr__(self, "y", _validate_finite_number("y", self.y))
        object.__setattr__(self, "heading_rad", _validate_finite_number("heading_rad", self.heading_rad))
        if not isinstance(self.source, str) or not self.source.strip():
            raise SLAMProviderError("source must not be empty")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(self.confidence)
        ):
            raise SLAMProviderError("confidence must be a finite number")
        if self.confidence < 0.0 or self.confidence > 1.0:
            raise SLAMProviderError("confidence must be between 0.0 and 1.0")
        if self.timestamp.tzinfo is None:
            raise SLAMProviderError("timestamp must be timezone-aware")
        if not isinstance(self.metadata, dict):
            raise SLAMProviderError("metadata must be a dictionary")

        object.__setattr__(self, "source", self.source.strip())
        object.__setattr__(self, "confidence", float(self.confidence))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "heading_rad": self.heading_rad,
            "source": self.source,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_quadruped_state(
        cls,
        state: QuadrupedState,
        source: str = "odometry_fallback",
        confidence: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> "CorrectedPosition":
        return cls(
            x=state.position[0],
            y=state.position[1],
            heading_rad=state.rpy[2],
            source=source,
            confidence=confidence,
            timestamp=state.timestamp,
            metadata=metadata or {},
        )


class SLAMProvider:
    """Corrected-position abstraction that safely falls back to odometry in Phase 1."""

    def __init__(
        self,
        state_monitor: StateMonitor | None = None,
        enabled: bool = False,
    ) -> None:
        self._state_monitor = state_monitor or get_state_monitor()
        self._enabled = bool(enabled)
        self._last_position: CorrectedPosition | None = None
        self._last_error: str | None = None
        self._read_count = 0

    async def get_corrected_position(self) -> CorrectedPosition:
        try:
            corrected_position: CorrectedPosition | None = None
            if self._enabled:
                corrected_position = await self._compute_corrected_position()

            if corrected_position is None:
                corrected_position = await self._fallback_to_odometry()

            self._last_position = corrected_position
            self._read_count += 1
            self._last_error = None
            return corrected_position
        except SLAMProviderError as exc:
            self._last_error = str(exc)
            logger.warning("SLAM provider could not produce corrected position", extra={"error": str(exc)})
            raise
        except Exception as exc:
            self._last_error = str(exc)
            logger.exception("SLAM provider failed unexpectedly")
            raise SLAMProviderError(f"Failed to get corrected position: {exc}") from exc

    async def get_last_position(self) -> CorrectedPosition | None:
        return self._last_position

    def read_count(self) -> int:
        return self._read_count

    def last_error(self) -> str | None:
        return self._last_error

    def is_enabled(self) -> bool:
        return self._enabled

    async def _compute_corrected_position(self) -> CorrectedPosition | None:
        return None

    async def _fallback_to_odometry(self) -> CorrectedPosition:
        state = await self._state_monitor.get_current_state()
        if state is None:
            state = await self._state_monitor.poll_once()
        if state is None:
            raise SLAMProviderError("Quadruped state is unavailable for corrected position fallback")

        logger.debug("SLAM provider falling back to odometry")
        return CorrectedPosition.from_quadruped_state(state)


slam_provider = SLAMProvider()


def get_slam_provider() -> SLAMProvider:
    return slam_provider


__all__ = [
    "CorrectedPosition",
    "SLAMProvider",
    "SLAMProviderError",
    "get_slam_provider",
    "slam_provider",
]
