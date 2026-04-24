from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def video_module():
    sys.modules.pop("hardware.video_reader", None)
    module = importlib.import_module("hardware.video_reader")
    return module


def test_video_frame_to_dict(video_module) -> None:
    frame = video_module.VideoFrame(
        frame_id="frame-1",
        timestamp=datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc),
        source="stub",
        width=640,
        height=480,
        data=b"not-serialized",
        metadata={"station_id": "A"},
    )

    assert frame.to_dict() == {
        "frame_id": "frame-1",
        "timestamp": "2026-04-24T14:00:00+00:00",
        "source": "stub",
        "width": 640,
        "height": 480,
        "has_data": True,
        "metadata": {"station_id": "A"},
    }


def test_video_frame_rejects_invalid_source(video_module) -> None:
    with pytest.raises(video_module.VideoReaderError, match="source"):
        video_module.VideoFrame(
            frame_id="frame-1",
            timestamp=datetime.now(timezone.utc),
            source="  ",
        )


def test_video_frame_rejects_invalid_dimensions(video_module) -> None:
    with pytest.raises(video_module.VideoReaderError, match="width"):
        video_module.VideoFrame(
            frame_id="frame-1",
            timestamp=datetime.now(timezone.utc),
            source="stub",
            width=0,
        )

    with pytest.raises(video_module.VideoReaderError, match="height"):
        video_module.VideoFrame(
            frame_id="frame-1",
            timestamp=datetime.now(timezone.utc),
            source="stub",
            height=-2,
        )


@pytest.mark.asyncio
async def test_reader_start_stop_idempotent(video_module) -> None:
    reader = video_module.VideoReader()

    await reader.start()
    await reader.start()
    assert reader.is_running() is True

    await reader.stop()
    await reader.stop()
    assert reader.is_running() is False


@pytest.mark.asyncio
async def test_read_once_returns_none_in_phase1(video_module) -> None:
    reader = video_module.VideoReader()

    assert await reader.read_once() is None


@pytest.mark.asyncio
async def test_get_latest_frame_returns_none_before_read(video_module) -> None:
    reader = video_module.VideoReader()

    assert await reader.get_latest_frame() is None


@pytest.mark.asyncio
async def test_read_count_increments(video_module) -> None:
    reader = video_module.VideoReader()

    assert reader.read_count() == 0
    await reader.read_once()
    await reader.read_once()
    assert reader.read_count() == 2


@pytest.mark.asyncio
async def test_enabled_true_still_safe_stub(video_module) -> None:
    reader = video_module.VideoReader(source="future-camera", enabled=True)

    await reader.start()
    frame = await reader.read_once()

    assert reader.is_enabled() is True
    assert reader.is_running() is True
    assert frame is None
    assert reader.last_error() is None


def test_global_get_video_reader_returns_reader(video_module) -> None:
    assert video_module.get_video_reader() is video_module.video_reader
