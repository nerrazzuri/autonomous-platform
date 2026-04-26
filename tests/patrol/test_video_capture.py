from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.mark.asyncio
async def test_start_stop_idempotent() -> None:
    from shared.hardware.video_reader import VideoReader

    from apps.patrol.observation.video_capture import VideoCapture

    capture = VideoCapture(video_reader=VideoReader(), dwell_seconds=0.0, sharpness_threshold=0.0)

    await capture.start()
    await capture.start()
    assert capture.is_running() is True

    await capture.stop()
    await capture.stop()
    assert capture.is_running() is False


@pytest.mark.asyncio
async def test_capture_returns_none_phase1() -> None:
    from shared.hardware.video_reader import VideoReader

    from apps.patrol.observation.video_capture import VideoCapture

    capture = VideoCapture(video_reader=VideoReader(), dwell_seconds=0.0, sharpness_threshold=0.0)

    frame = await capture.capture(zone_id="PLANTATION_NORTH")

    assert frame is None
    assert capture.last_error() is None


@pytest.mark.asyncio
async def test_capture_count_increments() -> None:
    from shared.hardware.video_reader import VideoReader

    from apps.patrol.observation.video_capture import VideoCapture

    capture = VideoCapture(video_reader=VideoReader(), dwell_seconds=0.0, sharpness_threshold=0.0)

    await capture.capture()
    await capture.capture()

    assert capture.capture_count() == 2


def test_invalid_dwell_rejected() -> None:
    from shared.hardware.video_reader import VideoReader

    from apps.patrol.observation.video_capture import VideoCaptureError, VideoCapture

    with pytest.raises(VideoCaptureError, match="dwell_seconds"):
        VideoCapture(video_reader=VideoReader(), dwell_seconds=-1.0, sharpness_threshold=0.0)


def test_invalid_sharpness_rejected() -> None:
    from shared.hardware.video_reader import VideoReader

    from apps.patrol.observation.video_capture import VideoCaptureError, VideoCapture

    with pytest.raises(VideoCaptureError, match="sharpness_threshold"):
        VideoCapture(video_reader=VideoReader(), dwell_seconds=0.0, sharpness_threshold=-1.0)


def test_global_get_video_capture_returns_capture() -> None:
    from apps.patrol.observation.video_capture import VideoCapture, get_video_capture, video_capture

    assert get_video_capture() is video_capture
    assert isinstance(video_capture, VideoCapture)
