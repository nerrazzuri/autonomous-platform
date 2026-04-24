from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_state(x: float = 1.5, y: float = 2.5, heading_rad: float = 0.75):
    from quadruped.sdk_adapter import QuadrupedMode
    from quadruped.state_monitor import QuadrupedState

    return QuadrupedState(
        timestamp=datetime.now(timezone.utc),
        battery_pct=100,
        position=(x, y, 0.0),
        rpy=(0.0, 0.0, heading_rad),
        control_mode=0,
        connection_ok=True,
        mode=QuadrupedMode.STANDING,
    )


class FakeStateMonitor:
    def __init__(self, state=None, poll_state=None):
        self.state = state
        self.poll_state = poll_state if poll_state is not None else state
        self.poll_called = False

    async def get_current_state(self):
        return self.state

    async def poll_once(self):
        self.poll_called = True
        return self.poll_state


def test_corrected_position_to_dict() -> None:
    from navigation.slam import CorrectedPosition

    timestamp = datetime.now(timezone.utc)
    position = CorrectedPosition(
        x=1.0,
        y=2.0,
        heading_rad=0.5,
        source="unit-test",
        confidence=0.8,
        timestamp=timestamp,
        metadata={"map": "factory"},
    )

    payload = position.to_dict()

    assert payload == {
        "x": 1.0,
        "y": 2.0,
        "heading_rad": 0.5,
        "source": "unit-test",
        "confidence": 0.8,
        "timestamp": timestamp.isoformat(),
        "metadata": {"map": "factory"},
    }


def test_corrected_position_from_quadruped_state() -> None:
    from navigation.slam import CorrectedPosition

    state = make_state(x=3.0, y=4.0, heading_rad=1.25)

    position = CorrectedPosition.from_quadruped_state(
        state,
        source="odometry_fallback",
        confidence=0.1,
        metadata={"reason": "phase1"},
    )

    assert position.x == 3.0
    assert position.y == 4.0
    assert position.heading_rad == 1.25
    assert position.timestamp == state.timestamp
    assert position.metadata == {"reason": "phase1"}


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_corrected_position_rejects_invalid_confidence(confidence: float) -> None:
    from navigation.slam import CorrectedPosition, SLAMProviderError

    with pytest.raises(SLAMProviderError):
        CorrectedPosition(
            x=0.0,
            y=0.0,
            heading_rad=0.0,
            source="bad-confidence",
            confidence=confidence,
            timestamp=datetime.now(timezone.utc),
            metadata={},
        )


@pytest.mark.asyncio
async def test_initial_last_position_is_none() -> None:
    from navigation.slam import SLAMProvider

    provider = SLAMProvider(state_monitor=FakeStateMonitor())

    assert await provider.get_last_position() is None
    assert provider.read_count() == 0


@pytest.mark.asyncio
async def test_get_corrected_position_uses_current_state() -> None:
    from navigation.slam import SLAMProvider

    state = make_state(x=7.0, y=8.0, heading_rad=0.25)
    monitor = FakeStateMonitor(state=state)
    provider = SLAMProvider(state_monitor=monitor)

    position = await provider.get_corrected_position()

    assert position.x == 7.0
    assert position.y == 8.0
    assert position.heading_rad == 0.25
    assert monitor.poll_called is False
    assert await provider.get_last_position() == position


@pytest.mark.asyncio
async def test_get_corrected_position_falls_back_to_poll_once() -> None:
    from navigation.slam import SLAMProvider

    state = make_state(x=2.0, y=3.0)
    monitor = FakeStateMonitor(state=None, poll_state=state)
    provider = SLAMProvider(state_monitor=monitor)

    position = await provider.get_corrected_position()

    assert monitor.poll_called is True
    assert position.x == 2.0
    assert position.y == 3.0


@pytest.mark.asyncio
async def test_get_corrected_position_raises_when_no_state_available() -> None:
    from navigation.slam import SLAMProvider, SLAMProviderError

    provider = SLAMProvider(state_monitor=FakeStateMonitor(state=None, poll_state=None))

    with pytest.raises(SLAMProviderError):
        await provider.get_corrected_position()


@pytest.mark.asyncio
async def test_read_count_increments() -> None:
    from navigation.slam import SLAMProvider

    provider = SLAMProvider(state_monitor=FakeStateMonitor(state=make_state()))

    await provider.get_corrected_position()
    await provider.get_corrected_position()

    assert provider.read_count() == 2


@pytest.mark.asyncio
async def test_enabled_flag_still_falls_back_safely_in_phase1() -> None:
    from navigation.slam import SLAMProvider

    provider = SLAMProvider(state_monitor=FakeStateMonitor(state=make_state(x=4.0)), enabled=True)

    position = await provider.get_corrected_position()

    assert provider.is_enabled() is True
    assert position.source == "odometry_fallback"
    assert position.x == 4.0


@pytest.mark.asyncio
async def test_last_error_set_on_failure() -> None:
    from navigation.slam import SLAMProvider, SLAMProviderError

    provider = SLAMProvider(state_monitor=FakeStateMonitor(state=None, poll_state=None))

    with pytest.raises(SLAMProviderError):
        await provider.get_corrected_position()

    assert provider.last_error() is not None
    assert "state" in provider.last_error().lower()


def test_global_get_slam_provider_returns_provider() -> None:
    from navigation.slam import SLAMProvider, get_slam_provider

    assert isinstance(get_slam_provider(), SLAMProvider)
