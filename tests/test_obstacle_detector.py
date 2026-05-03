from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Fake helpers — mimic ROS2 LaserScan shape without importing rclpy
# ---------------------------------------------------------------------------

def _make_scan(
    ranges: list,
    angle_min: float = -math.pi / 2,
    angle_increment: float = math.pi / 180,  # 1° steps
    range_min: float = 0.1,
    range_max: float = 30.0,
):
    return SimpleNamespace(
        ranges=ranges,
        angle_min=angle_min,
        angle_increment=angle_increment,
        range_min=range_min,
        range_max=range_max,
    )


class FakeBridge:
    def __init__(self, scan=None):
        self._scan = scan

    def get_latest_scan(self):
        return self._scan


# ---------------------------------------------------------------------------
# _check_forward_arc — pure helper tests, no bridge needed
# ---------------------------------------------------------------------------

def test_check_forward_arc_detects_obstacle_directly_ahead():
    from shared.navigation.obstacle import _check_forward_arc
    # 181 rays from -90° to +90° at 1° steps; index 90 → angle = 0° (ahead)
    ranges = [0.0] * 90 + [0.5] + [0.0] * 90
    scan = _make_scan(ranges=ranges, angle_min=-math.pi / 2)
    assert _check_forward_arc(scan, stop_distance_m=0.8, arc_half_deg=45.0) is True


def test_check_forward_arc_ignores_obstacle_outside_arc():
    from shared.navigation.obstacle import _check_forward_arc
    # angle_min=0.8 rad (~46°) → all rays outside ±45° arc
    scan = _make_scan(ranges=[0.5] * 10, angle_min=0.8, angle_increment=0.1)
    assert _check_forward_arc(scan, stop_distance_m=0.8, arc_half_deg=45.0) is False


def test_check_forward_arc_ignores_nan_inf_zero_negative():
    from shared.navigation.obstacle import _check_forward_arc
    bad = [float("nan"), float("inf"), float("-inf"), 0.0, -1.0]
    scan = _make_scan(ranges=bad, angle_min=-0.1, angle_increment=0.04)
    assert _check_forward_arc(scan, stop_distance_m=0.8, arc_half_deg=45.0) is False


def test_check_forward_arc_ignores_below_range_min():
    from shared.navigation.obstacle import _check_forward_arc
    # range_min=0.3, range=0.2 → below minimum
    scan = _make_scan(ranges=[0.2], angle_min=0.0, angle_increment=0.1, range_min=0.3)
    assert _check_forward_arc(scan, stop_distance_m=0.8, arc_half_deg=45.0) is False


def test_check_forward_arc_ignores_above_range_max():
    from shared.navigation.obstacle import _check_forward_arc
    # range_max=10.0, range=15.0 → above maximum; stop_distance large enough not to filter
    scan = _make_scan(ranges=[15.0], angle_min=0.0, angle_increment=0.1, range_max=10.0)
    assert _check_forward_arc(scan, stop_distance_m=20.0, arc_half_deg=45.0) is False


def test_check_forward_arc_detects_exactly_at_stop_distance():
    from shared.navigation.obstacle import _check_forward_arc
    scan = _make_scan(ranges=[0.8], angle_min=0.0, angle_increment=0.1)
    assert _check_forward_arc(scan, stop_distance_m=0.8, arc_half_deg=45.0) is True


# ---------------------------------------------------------------------------
# ObstacleDetector bridge integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_returns_clear_when_bridge_missing(monkeypatch):
    import shared.ros2 as ros2_mod
    from shared.navigation.obstacle import ObstacleDetector
    monkeypatch.setattr(ros2_mod, "_bridge", None)
    detector = ObstacleDetector()
    status = await detector._detect_obstacle()
    assert status.obstacle_present is False


@pytest.mark.asyncio
async def test_detect_returns_clear_when_scan_none(monkeypatch):
    import shared.ros2 as ros2_mod
    from shared.navigation.obstacle import ObstacleDetector
    monkeypatch.setattr(ros2_mod, "_bridge", FakeBridge(scan=None))
    detector = ObstacleDetector()
    status = await detector._detect_obstacle()
    assert status.obstacle_present is False


@pytest.mark.asyncio
async def test_detect_returns_detected_for_obstacle_in_arc(monkeypatch):
    import shared.ros2 as ros2_mod
    from shared.navigation.obstacle import ObstacleDetector
    # Index 90 → angle=0° (directly ahead), 0.5m within 0.8m stop distance
    ranges = [0.0] * 90 + [0.5] + [0.0] * 90
    scan = _make_scan(ranges=ranges, angle_min=-math.pi / 2)
    monkeypatch.setattr(ros2_mod, "_bridge", FakeBridge(scan=scan))
    detector = ObstacleDetector(stop_distance_m=0.8, forward_arc_deg=90.0)
    status = await detector._detect_obstacle()
    assert status.obstacle_present is True
    assert status.source == "m10_lidar"
    assert status.confidence == 1.0


@pytest.mark.asyncio
async def test_poll_once_publishes_obstacle_transition_events(monkeypatch):
    import shared.ros2 as ros2_mod
    import shared.navigation.obstacle as obstacle_module
    from shared.core.event_bus import EventName
    from shared.navigation.obstacle import ObstacleDetector

    events = []

    class FakeEventBus:
        def publish_nowait(self, event_name, payload=None, **kwargs):
            events.append((event_name, payload or {}, kwargs))

    clear_ranges = [2.0] * 181
    obstacle_ranges = [2.0] * 90 + [0.5] + [2.0] * 90
    bridge = FakeBridge(scan=_make_scan(ranges=obstacle_ranges, angle_min=-math.pi / 2))
    monkeypatch.setattr(ros2_mod, "_bridge", bridge)
    monkeypatch.setattr(obstacle_module, "get_event_bus", lambda: FakeEventBus(), raising=False)

    detector = ObstacleDetector(stop_distance_m=0.8, forward_arc_deg=90.0)
    detected = await detector.poll_once()

    bridge._scan = _make_scan(ranges=clear_ranges, angle_min=-math.pi / 2)
    cleared = await detector.poll_once()

    assert detected.obstacle_present is True
    assert cleared.obstacle_present is False
    assert [event[0] for event in events] == [
        EventName.OBSTACLE_DETECTED,
        EventName.OBSTACLE_CLEARED,
    ]
    assert events[0][1]["source"] == "m10_lidar"
    assert events[1][1]["obstacle_present"] is False


@pytest.mark.asyncio
async def test_detect_returns_clear_for_invalid_scan_shape(monkeypatch):
    import shared.ros2 as ros2_mod
    from shared.navigation.obstacle import ObstacleDetector
    bad_scan = SimpleNamespace(ranges=None)  # iterating None raises TypeError
    monkeypatch.setattr(ros2_mod, "_bridge", FakeBridge(scan=bad_scan))
    detector = ObstacleDetector()
    status = await detector._detect_obstacle()
    assert status.obstacle_present is False


def test_config_defaults_parse_correctly():
    from shared.core.config import NavigationSection
    nav = NavigationSection()
    assert nav.obstacle_stop_distance_m == 0.8
    assert nav.obstacle_forward_arc_deg == 90.0
