from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_zone():
    from apps.patrol.observation.zone_config import ZoneDefinition

    return ZoneDefinition(
        zone_id="PLANTATION_NORTH",
        description="North plantation",
        normal_objects=["trees"],
        suspicious_objects=["unknown vehicle"],
        threat_objects=["fire"],
    )


def make_frame():
    from shared.hardware.video_reader import VideoFrame

    return VideoFrame(
        frame_id="frame-1",
        timestamp=datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
        source="stub",
        metadata={"zone_id": "PLANTATION_NORTH"},
    )


@pytest.mark.asyncio
async def test_disabled_returns_stub_without_api() -> None:
    from apps.patrol.observation.vision_analyser import VisionAnalyser

    analyser = VisionAnalyser(enabled=False, provider="claude", max_tokens=100, api_timeout_seconds=5.0)

    result = await analyser.analyse(make_frame(), make_zone())

    assert result.analysis_source == "stub"
    assert result.objects_detected == []
    assert result.error is None


@pytest.mark.asyncio
async def test_none_frame_returns_stub_error() -> None:
    from apps.patrol.observation.vision_analyser import VisionAnalyser

    analyser = VisionAnalyser(enabled=True, provider="claude", max_tokens=100, api_timeout_seconds=5.0)

    result = await analyser.analyse(None, make_zone())

    assert result.analysis_source == "stub"
    assert result.objects_detected == []
    assert result.error == "no frame"


def test_parse_json_list() -> None:
    from apps.patrol.observation.vision_analyser import VisionAnalyser

    analyser = VisionAnalyser(enabled=False, provider="none", max_tokens=100, api_timeout_seconds=5.0)

    objects = analyser._parse_detected_objects(
        '[{"label":"person","threat_level":"SUSPICIOUS","confidence":0.9,"reason":"Unexpected"}]'
    )

    assert len(objects) == 1
    assert objects[0].label == "person"


def test_parse_json_object_with_objects() -> None:
    from apps.patrol.observation.vision_analyser import VisionAnalyser

    analyser = VisionAnalyser(enabled=False, provider="none", max_tokens=100, api_timeout_seconds=5.0)

    objects = analyser._parse_detected_objects(
        '{"objects":[{"label":"fire","threat_level":"THREAT","confidence":0.95,"reason":"Smoke and flame"}]}'
    )

    assert len(objects) == 1
    assert objects[0].threat_level == "THREAT"


def test_parse_markdown_fenced_json() -> None:
    from apps.patrol.observation.vision_analyser import VisionAnalyser

    analyser = VisionAnalyser(enabled=False, provider="none", max_tokens=100, api_timeout_seconds=5.0)

    objects = analyser._parse_detected_objects(
        '```json\n[{"label":"vehicle","threat_level":"SUSPICIOUS","confidence":0.7,"reason":"Unknown"}]\n```'
    )

    assert len(objects) == 1
    assert objects[0].label == "vehicle"


def test_malformed_json_returns_error_or_raises_helper() -> None:
    from apps.patrol.observation.vision_analyser import VisionAnalyser, VisionAnalyserError

    analyser = VisionAnalyser(enabled=False, provider="none", max_tokens=100, api_timeout_seconds=5.0)

    with pytest.raises(VisionAnalyserError):
        analyser._parse_detected_objects("not json")


@pytest.mark.asyncio
async def test_claude_failure_conservative_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.patrol.observation.vision_analyser import VisionAnalyser, VisionAnalyserError

    analyser = VisionAnalyser(
        enabled=True,
        provider="claude",
        max_tokens=100,
        api_timeout_seconds=5.0,
        offline_fallback_mode="conservative",
    )

    async def raise_not_implemented(_frame, _zone):
        raise VisionAnalyserError("boom")

    monkeypatch.setattr(analyser, "_analyse_with_claude", raise_not_implemented)

    result = await analyser.analyse(make_frame(), make_zone())

    assert result.analysis_source == "offline_conservative"
    assert result.error == "boom"
    assert len(result.objects_detected) == 1
    assert result.objects_detected[0].label == "unknown"


def test_provider_validation() -> None:
    from apps.patrol.observation.vision_analyser import VisionAnalyser, VisionAnalyserError

    with pytest.raises(VisionAnalyserError, match="provider"):
        VisionAnalyser(enabled=False, provider="bad", max_tokens=100, api_timeout_seconds=5.0)


def test_global_get_vision_analyser_returns_analyser() -> None:
    from apps.patrol.observation.vision_analyser import VisionAnalyser, get_vision_analyser, vision_analyser

    assert get_vision_analyser() is vision_analyser
    assert isinstance(vision_analyser, VisionAnalyser)
