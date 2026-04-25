from __future__ import annotations

"""Safe Phase 1 QR anchor contract for future floor-marker localization."""

from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from typing import Any

from shared.core.logger import get_logger
from shared.hardware.video_reader import VideoFrame


logger = get_logger(__name__)


class QRAnchorError(Exception):
    """Raised when QR anchor inputs or correction payloads are invalid."""


def _normalize_marker_id(marker_id: str) -> str:
    if not isinstance(marker_id, str) or not marker_id.strip():
        raise QRAnchorError("marker_id must not be empty")
    return marker_id.strip()


def _normalize_source(source: str) -> str:
    if not isinstance(source, str) or not source.strip():
        raise QRAnchorError("source must not be empty")
    return source.strip()


def _normalize_float(name: str, value: float) -> float:
    numeric = float(value)
    if not isfinite(numeric):
        raise QRAnchorError(f"{name} must be finite")
    return numeric


def _normalize_confidence(confidence: float) -> float:
    numeric = float(confidence)
    if not isfinite(numeric) or numeric < 0.0 or numeric > 1.0:
        raise QRAnchorError("confidence must be between 0.0 and 1.0")
    return numeric


def _normalize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        raise QRAnchorError("metadata must be a dictionary")
    return dict(metadata)


@dataclass(frozen=True)
class CorrectionResult:
    marker_id: str
    x: float
    y: float
    heading_rad: float
    confidence: float
    timestamp: datetime
    source: str = "qr_anchor"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "marker_id", _normalize_marker_id(self.marker_id))
        object.__setattr__(self, "x", _normalize_float("x", self.x))
        object.__setattr__(self, "y", _normalize_float("y", self.y))
        object.__setattr__(self, "heading_rad", _normalize_float("heading_rad", self.heading_rad))
        object.__setattr__(self, "confidence", _normalize_confidence(self.confidence))
        if not isinstance(self.timestamp, datetime):
            raise QRAnchorError("timestamp must be a datetime")
        object.__setattr__(self, "source", _normalize_source(self.source))
        object.__setattr__(self, "metadata", _normalize_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "marker_id": self.marker_id,
            "x": self.x,
            "y": self.y,
            "heading_rad": self.heading_rad,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "metadata": dict(self.metadata),
        }


class QRAnchorReader:
    """Phase 1 no-op QR correction reader with a future decode hook."""

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = bool(enabled)
        self._read_count = 0
        self._last_correction: CorrectionResult | None = None
        self._last_error: str | None = None

    async def check_frame(self, frame: VideoFrame | Any | None) -> CorrectionResult | None:
        self._read_count += 1
        try:
            correction = await self._decode_frame(frame)
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning(
                "QR anchor decode failed",
                extra={
                    "enabled": self._enabled,
                    "read_count": self._read_count,
                    "error": self._last_error,
                    "frame_type": type(frame).__name__ if frame is not None else "NoneType",
                },
            )
            raise QRAnchorError(str(exc)) from exc

        self._last_correction = correction
        self._last_error = None
        logger.info(
            "QR anchor check_frame completed (Phase 1 stub)",
            extra={
                "enabled": self._enabled,
                "read_count": self._read_count,
                "has_correction": correction is not None,
                "frame_type": type(frame).__name__ if frame is not None else "NoneType",
            },
        )
        return correction

    async def get_last_correction(self) -> CorrectionResult | None:
        return self._last_correction

    def is_enabled(self) -> bool:
        return self._enabled

    def read_count(self) -> int:
        return self._read_count

    def last_error(self) -> str | None:
        return self._last_error

    async def _decode_frame(self, frame: VideoFrame | Any | None) -> CorrectionResult | None:
        return None


qr_anchor_reader = QRAnchorReader()


def get_qr_anchor_reader() -> QRAnchorReader:
    return qr_anchor_reader


__all__ = [
    "CorrectionResult",
    "QRAnchorError",
    "QRAnchorReader",
    "get_qr_anchor_reader",
    "qr_anchor_reader",
]
