from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_frame():
    from shared.hardware.video_reader import VideoFrame

    return VideoFrame(
        frame_id="frame-1",
        timestamp=datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
        source="stub",
        metadata={"zone_id": "PLANTATION_NORTH"},
    )


@pytest.mark.asyncio
async def test_unavailable_when_ultralytics_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.patrol.observation.local_yolo_analyser as module

    analyser = module.LocalYOLOAnalyser()

    def fake_import(name: str):
        raise ImportError(name)

    monkeypatch.setattr(module.importlib, "import_module", fake_import)

    with pytest.raises(module.LocalYOLOUnavailableError):
        await analyser.analyse(make_frame())


def test_global_or_class_import_safe_without_ultralytics() -> None:
    from apps.patrol.observation.local_yolo_analyser import (
        LocalYOLOAnalyser,
        LocalYOLOUnavailableError,
    )

    analyser = LocalYOLOAnalyser()

    assert isinstance(analyser, LocalYOLOAnalyser)
    assert issubclass(LocalYOLOUnavailableError, Exception)
