from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_obstacle_status_to_dict() -> None:
    from navigation.obstacle import ObstacleStatus

    status = ObstacleStatus.detected(
        source="test-detector",
        confidence=0.75,
        metadata={"zone": "corridor"},
    )
    payload = status.to_dict()

    assert payload["obstacle_present"] is True
    assert payload["source"] == "test-detector"
    assert payload["confidence"] == 0.75
    assert isinstance(payload["timestamp"], str)
    assert payload["metadata"] == {"zone": "corridor"}


def test_obstacle_status_clear_factory() -> None:
    from navigation.obstacle import ObstacleStatus

    status = ObstacleStatus.clear(source="unit-test")

    assert status.obstacle_present is False
    assert status.source == "unit-test"
    assert status.confidence == 0.0
    assert isinstance(status.timestamp, datetime)
    assert status.metadata == {}


def test_obstacle_status_detected_factory() -> None:
    from navigation.obstacle import ObstacleStatus

    status = ObstacleStatus.detected(source="future-detector", confidence=1.0)

    assert status.obstacle_present is True
    assert status.source == "future-detector"
    assert status.confidence == 1.0


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_obstacle_status_rejects_invalid_confidence(confidence: float) -> None:
    from navigation.obstacle import ObstacleDetectorError, ObstacleStatus

    with pytest.raises(ObstacleDetectorError):
        ObstacleStatus.detected(source="bad-confidence", confidence=confidence)


@pytest.mark.asyncio
async def test_detector_initial_status_is_clear() -> None:
    from navigation.obstacle import ObstacleDetector

    detector = ObstacleDetector(polling_interval_seconds=0.01)

    status = await detector.get_status()

    assert status.obstacle_present is False
    assert detector.is_running() is False


def test_invalid_polling_interval_rejected() -> None:
    from navigation.obstacle import ObstacleDetector, ObstacleDetectorError

    with pytest.raises(ObstacleDetectorError):
        ObstacleDetector(polling_interval_seconds=0.0)


@pytest.mark.asyncio
async def test_poll_once_returns_clear_in_phase1_stub() -> None:
    from navigation.obstacle import ObstacleDetector

    detector = ObstacleDetector(polling_interval_seconds=0.01)

    status = await detector.poll_once()

    assert status.obstacle_present is False
    assert status.source == "null_detector"


@pytest.mark.asyncio
async def test_get_status_before_poll_is_clear() -> None:
    from navigation.obstacle import ObstacleDetector

    detector = ObstacleDetector(polling_interval_seconds=0.01)

    status = await detector.get_status()

    assert status.obstacle_present is False


@pytest.mark.asyncio
async def test_start_and_stop_are_idempotent() -> None:
    from navigation.obstacle import ObstacleDetector

    detector = ObstacleDetector(polling_interval_seconds=0.01)

    await detector.start()
    await detector.start()
    await asyncio.sleep(0.03)
    assert detector.is_running() is True

    await detector.stop()
    await detector.stop()
    assert detector.is_running() is False


@pytest.mark.asyncio
async def test_stop_before_start_does_not_crash() -> None:
    from navigation.obstacle import ObstacleDetector

    detector = ObstacleDetector(polling_interval_seconds=0.01)

    await detector.stop()

    assert detector.is_running() is False


@pytest.mark.asyncio
async def test_poll_count_increments() -> None:
    from navigation.obstacle import ObstacleDetector

    detector = ObstacleDetector(polling_interval_seconds=0.01)

    await detector.poll_once()
    await detector.poll_once()

    assert detector.poll_count() == 2


@pytest.mark.asyncio
async def test_poll_loop_continues_after_failure_in_overridden_detector() -> None:
    from navigation.obstacle import ObstacleDetector, ObstacleStatus

    class FailingObstacleDetector(ObstacleDetector):
        def __init__(self) -> None:
            super().__init__(polling_interval_seconds=0.01)
            self.failed_once = False

        async def _detect_obstacle(self) -> ObstacleStatus:
            if not self.failed_once:
                self.failed_once = True
                raise RuntimeError("detector failure")
            return ObstacleStatus.clear(source="failing-detector")

    detector = FailingObstacleDetector()

    await detector.start()
    await asyncio.sleep(0.05)
    await detector.stop()

    status = await detector.get_status()
    assert detector.last_error() == "detector failure"
    assert detector.poll_count() >= 1
    assert status.source == "failing-detector"


def test_global_get_obstacle_detector_returns_detector() -> None:
    from navigation.obstacle import ObstacleDetector, get_obstacle_detector

    assert isinstance(get_obstacle_detector(), ObstacleDetector)
