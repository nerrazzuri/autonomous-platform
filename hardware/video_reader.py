from __future__ import annotations

"""Safe Phase 1 video reader contract for future quadruped camera integration."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.logger import get_logger


logger = get_logger(__name__)


class VideoReaderError(Exception):
    """Raised when video reader configuration or frame data is invalid."""


def _normalize_source(source: str) -> str:
    if not isinstance(source, str) or not source.strip():
        raise VideoReaderError("source must not be empty")
    return source.strip()


def _normalize_dimension(name: str, value: int | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise VideoReaderError(f"{name} must be a positive integer when provided")
    return value


def _normalize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        raise VideoReaderError("metadata must be a dictionary")
    return dict(metadata)


@dataclass(frozen=True)
class VideoFrame:
    frame_id: str
    timestamp: datetime
    source: str
    width: int | None = None
    height: int | None = None
    data: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _normalize_source(self.source))
        if not isinstance(self.timestamp, datetime):
            raise VideoReaderError("timestamp must be a datetime")
        object.__setattr__(self, "width", _normalize_dimension("width", self.width))
        object.__setattr__(self, "height", _normalize_dimension("height", self.height))
        object.__setattr__(self, "metadata", _normalize_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "width": self.width,
            "height": self.height,
            "has_data": self.data is not None,
            "metadata": dict(self.metadata),
        }


class VideoReader:
    """Phase 1 no-op video reader with a stable async contract."""

    def __init__(self, source: str = "stub", enabled: bool = False) -> None:
        self._source = _normalize_source(source)
        self._enabled = bool(enabled)
        self._running = False
        self._read_count = 0
        self._latest_frame: VideoFrame | None = None
        self._last_error: str | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        logger.info(
            "Video reader started (Phase 1 no-op)",
            extra={"source": self._source, "enabled": self._enabled},
        )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        logger.info(
            "Video reader stopped (Phase 1 no-op)",
            extra={"source": self._source, "enabled": self._enabled},
        )

    async def get_latest_frame(self) -> VideoFrame | None:
        return self._latest_frame

    async def read_once(self) -> VideoFrame | None:
        self._read_count += 1
        try:
            frame = await self._read_frame()
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning(
                "Video reader read failed",
                extra={"source": self._source, "enabled": self._enabled, "error": self._last_error},
            )
            raise VideoReaderError(str(exc)) from exc

        self._latest_frame = frame
        self._last_error = None
        logger.info(
            "Video reader read_once completed (Phase 1 stub)",
            extra={
                "source": self._source,
                "enabled": self._enabled,
                "read_count": self._read_count,
                "has_frame": frame is not None,
            },
        )
        return frame

    def is_running(self) -> bool:
        return self._running

    def is_enabled(self) -> bool:
        return self._enabled

    def read_count(self) -> int:
        return self._read_count

    def last_error(self) -> str | None:
        return self._last_error

    async def _read_frame(self) -> VideoFrame | None:
        return None


video_reader = VideoReader()


def get_video_reader() -> VideoReader:
    return video_reader


__all__ = [
    "VideoFrame",
    "VideoReader",
    "VideoReaderError",
    "get_video_reader",
    "video_reader",
]
