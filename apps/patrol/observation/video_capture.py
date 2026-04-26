from __future__ import annotations

from shared.core.config import get_config
from shared.core.logger import get_logger
from shared.hardware.video_reader import VideoFrame, VideoReader, get_video_reader


logger = get_logger(__name__)


class VideoCaptureError(Exception):
    """Raised when patrol video capture configuration or execution fails."""


class VideoCapture:
    def __init__(
        self,
        video_reader: VideoReader | None = None,
        dwell_seconds: float | None = None,
        sharpness_threshold: float | None = None,
    ) -> None:
        config = get_config()
        resolved_dwell = config.patrol.observation_dwell_seconds if dwell_seconds is None else dwell_seconds
        resolved_sharpness = config.vision.sharpness_threshold if sharpness_threshold is None else sharpness_threshold

        if not isinstance(resolved_dwell, (int, float)) or float(resolved_dwell) < 0:
            raise VideoCaptureError("dwell_seconds must be >= 0")
        if not isinstance(resolved_sharpness, (int, float)) or float(resolved_sharpness) < 0:
            raise VideoCaptureError("sharpness_threshold must be >= 0")

        self._video_reader = video_reader or get_video_reader()
        self._default_dwell_seconds = float(resolved_dwell)
        self._sharpness_threshold = float(resolved_sharpness)
        self._running = False
        self._capture_count = 0
        self._last_error: str | None = None

    async def start(self) -> None:
        if self._running:
            return
        await self._video_reader.start()
        self._running = True

    async def stop(self) -> None:
        if not self._running:
            return
        await self._video_reader.stop()
        self._running = False

    async def capture(self, dwell_seconds: float | None = None, zone_id: str | None = None) -> VideoFrame | None:
        resolved_dwell = self._default_dwell_seconds if dwell_seconds is None else dwell_seconds
        if not isinstance(resolved_dwell, (int, float)) or float(resolved_dwell) < 0:
            raise VideoCaptureError("dwell_seconds must be >= 0")

        self._capture_count += 1
        logger.debug(
            "Patrol video capture requested",
            extra={"zone_id": zone_id, "dwell_seconds": float(resolved_dwell), "capture_count": self._capture_count},
        )

        try:
            frame = await self._capture_frames(float(resolved_dwell), zone_id=zone_id)
        except Exception as exc:
            self._last_error = str(exc)
            raise VideoCaptureError(str(exc)) from exc

        self._last_error = None
        return frame

    def is_running(self) -> bool:
        return self._running

    def capture_count(self) -> int:
        return self._capture_count

    def last_error(self) -> str | None:
        return self._last_error

    async def _capture_frames(self, dwell_seconds: float, zone_id: str | None = None) -> VideoFrame | None:
        _ = dwell_seconds
        _ = zone_id
        return None


video_capture = VideoCapture()


def get_video_capture() -> VideoCapture:
    return video_capture


__all__ = [
    "VideoCapture",
    "VideoCaptureError",
    "get_video_capture",
    "video_capture",
]
