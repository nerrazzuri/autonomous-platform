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
# Fake helpers — mimic ROS2 message shape without importing rclpy
# ---------------------------------------------------------------------------

def _make_pose_msg(
    x: float = 0.0,
    y: float = 0.0,
    qx: float = 0.0,
    qy: float = 0.0,
    qz: float = 0.0,
    qw: float = 1.0,
    covariance: list | None = None,
):
    if covariance is None:
        covariance = [0.0] * 36
    position = SimpleNamespace(x=x, y=y, z=0.0)
    orientation = SimpleNamespace(x=qx, y=qy, z=qz, w=qw)
    pose_inner = SimpleNamespace(position=position, orientation=orientation)
    pose_outer = SimpleNamespace(pose=pose_inner, covariance=covariance)
    return SimpleNamespace(pose=pose_outer)


class FakeBridge:
    def __init__(self, pose_msg=None):
        self._pose_msg = pose_msg

    def get_latest_pose(self):
        return self._pose_msg


class FakeStateMonitor:
    async def get_current_state(self):
        return None

    async def poll_once(self):
        return None


# ---------------------------------------------------------------------------
# _quat_to_yaw — pure helper, no bridge needed
# ---------------------------------------------------------------------------

def test_quat_to_yaw_identity_is_zero():
    from shared.navigation.slam import _quat_to_yaw
    assert abs(_quat_to_yaw(0.0, 0.0, 0.0, 1.0)) < 1e-9


def test_quat_to_yaw_90_degrees():
    from shared.navigation.slam import _quat_to_yaw
    half = math.pi / 4
    yaw = _quat_to_yaw(0.0, 0.0, math.sin(half), math.cos(half))
    assert abs(yaw - math.pi / 2) < 1e-6


# ---------------------------------------------------------------------------
# _compute_corrected_position — bridge monkeypatched
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compute_returns_none_when_bridge_is_none(monkeypatch):
    import shared.ros2 as ros2_mod
    from shared.navigation.slam import SLAMProvider
    monkeypatch.setattr(ros2_mod, "_bridge", None)
    provider = SLAMProvider(state_monitor=FakeStateMonitor(), enabled=True)
    result = await provider._compute_corrected_position()
    assert result is None


@pytest.mark.asyncio
async def test_compute_returns_none_when_pose_is_none(monkeypatch):
    import shared.ros2 as ros2_mod
    from shared.navigation.slam import SLAMProvider
    monkeypatch.setattr(ros2_mod, "_bridge", FakeBridge(pose_msg=None))
    provider = SLAMProvider(state_monitor=FakeStateMonitor(), enabled=True)
    result = await provider._compute_corrected_position()
    assert result is None


@pytest.mark.asyncio
async def test_compute_identity_quaternion_heading_is_zero(monkeypatch):
    import shared.ros2 as ros2_mod
    from shared.navigation.slam import SLAMProvider
    msg = _make_pose_msg(x=1.5, y=2.5, qx=0.0, qy=0.0, qz=0.0, qw=1.0)
    monkeypatch.setattr(ros2_mod, "_bridge", FakeBridge(pose_msg=msg))
    provider = SLAMProvider(state_monitor=FakeStateMonitor(), enabled=True)
    result = await provider._compute_corrected_position()
    assert result is not None
    assert result.x == 1.5
    assert result.y == 2.5
    assert abs(result.heading_rad) < 1e-9
    assert result.source == "slam_toolbox"
    assert result.timestamp.tzinfo is not None


@pytest.mark.asyncio
async def test_compute_90_degree_yaw(monkeypatch):
    import shared.ros2 as ros2_mod
    from shared.navigation.slam import SLAMProvider
    half = math.pi / 4
    msg = _make_pose_msg(qz=math.sin(half), qw=math.cos(half))
    monkeypatch.setattr(ros2_mod, "_bridge", FakeBridge(pose_msg=msg))
    provider = SLAMProvider(state_monitor=FakeStateMonitor(), enabled=True)
    result = await provider._compute_corrected_position()
    assert result is not None
    assert abs(result.heading_rad - math.pi / 2) < 1e-6


@pytest.mark.asyncio
async def test_compute_high_confidence_for_low_covariance(monkeypatch):
    import shared.ros2 as ros2_mod
    from shared.navigation.slam import SLAMProvider
    cov = [0.0] * 36  # cov[0]=0, cov[7]=0 → variance=0 → confidence=1.0
    msg = _make_pose_msg(covariance=cov)
    monkeypatch.setattr(ros2_mod, "_bridge", FakeBridge(pose_msg=msg))
    provider = SLAMProvider(state_monitor=FakeStateMonitor(), enabled=True)
    result = await provider._compute_corrected_position()
    assert result is not None
    assert abs(result.confidence - 1.0) < 1e-9


@pytest.mark.asyncio
async def test_compute_lower_confidence_for_high_covariance(monkeypatch):
    import shared.ros2 as ros2_mod
    from shared.navigation.slam import SLAMProvider
    cov = [0.0] * 36
    cov[0] = 1.0  # x variance
    cov[7] = 1.0  # y variance → variance=2.0 → confidence=0.0
    msg = _make_pose_msg(covariance=cov)
    monkeypatch.setattr(ros2_mod, "_bridge", FakeBridge(pose_msg=msg))
    provider = SLAMProvider(state_monitor=FakeStateMonitor(), enabled=True)
    result = await provider._compute_corrected_position()
    assert result is not None
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_compute_returns_none_for_invalid_pose_shape(monkeypatch):
    import shared.ros2 as ros2_mod
    from shared.navigation.slam import SLAMProvider
    bad_msg = SimpleNamespace(pose=None)  # .pose.pose.position will raise AttributeError
    monkeypatch.setattr(ros2_mod, "_bridge", FakeBridge(pose_msg=bad_msg))
    provider = SLAMProvider(state_monitor=FakeStateMonitor(), enabled=True)
    result = await provider._compute_corrected_position()
    assert result is None
