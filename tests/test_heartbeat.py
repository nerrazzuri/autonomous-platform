from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path

import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeAdapter:
    def __init__(self):
        self.moves = []
        self.stop_motion_calls = 0
        self.fail_next_move = False
        self.raise_next_move = False

    async def move(self, vx, vy, yaw_rate):
        self.moves.append((vx, vy, yaw_rate))
        if self.raise_next_move:
            self.raise_next_move = False
            raise RuntimeError("move exploded")
        if self.fail_next_move:
            self.fail_next_move = False
            return False
        return True

    async def stop_motion(self):
        self.stop_motion_calls += 1
        self.moves.append((0.0, 0.0, 0.0))
        return True


@pytest_asyncio.fixture
async def heartbeat_env(monkeypatch: pytest.MonkeyPatch):
    from core.event_bus import EventBus
    import quadruped.heartbeat as heartbeat_module

    event_bus = EventBus()
    await event_bus.start()
    monkeypatch.setattr(heartbeat_module, "get_event_bus", lambda: event_bus)

    adapter = FakeAdapter()
    controller = heartbeat_module.HeartbeatController(sdk_adapter=adapter, interval_seconds=0.01)
    yield controller, adapter, event_bus, heartbeat_module
    await controller.stop()
    await event_bus.stop()


def test_velocity_command_zero() -> None:
    from quadruped.heartbeat import VelocityCommand

    command = VelocityCommand.zero(source="heartbeat")

    assert command.vx == 0.0
    assert command.vy == 0.0
    assert command.yaw_rate == 0.0
    assert command.source == "heartbeat"
    assert command.timestamp is not None
    assert command.timestamp.tzinfo is not None


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_velocity_command_rejects_nan_or_inf(value: float) -> None:
    from quadruped.heartbeat import HeartbeatError, VelocityCommand

    with pytest.raises(HeartbeatError):
        VelocityCommand(vx=value, vy=0.0, yaw_rate=0.0)


@pytest.mark.asyncio
async def test_controller_initial_target_is_zero(heartbeat_env) -> None:
    controller, _, _, _ = heartbeat_env

    command = await controller.get_target_velocity()

    assert command.vx == 0.0
    assert command.vy == 0.0
    assert command.yaw_rate == 0.0
    assert command.source == "heartbeat"
    assert command.timestamp is not None


@pytest.mark.asyncio
async def test_start_and_stop_are_idempotent(heartbeat_env) -> None:
    controller, _, _, _ = heartbeat_env

    await controller.start()
    assert controller.is_running() is True
    first_task = controller._task

    await controller.start()
    assert controller._task is first_task

    await controller.stop()
    await controller.stop()
    assert controller.is_running() is False


@pytest.mark.asyncio
async def test_stop_before_start_does_not_crash(heartbeat_env) -> None:
    controller, _, _, _ = heartbeat_env

    await controller.stop()

    assert controller.is_running() is False


@pytest.mark.asyncio
async def test_set_target_velocity_updates_target(heartbeat_env) -> None:
    controller, _, _, _ = heartbeat_env

    command = await controller.set_target_velocity(0.1, 0.2, 0.3, source="nav", task_id="task-1")
    stored = await controller.get_target_velocity()

    assert command == stored
    assert stored.source == "nav"
    assert stored.task_id == "task-1"


@pytest.mark.asyncio
async def test_clear_target_velocity_sets_zero(heartbeat_env) -> None:
    controller, _, _, _ = heartbeat_env
    await controller.set_target_velocity(0.1, 0.2, 0.3, source="nav")

    cleared = await controller.clear_target_velocity(source="heartbeat")

    assert cleared.vx == 0.0
    assert cleared.vy == 0.0
    assert cleared.yaw_rate == 0.0
    assert cleared.source == "heartbeat"
    stored = await controller.get_target_velocity()
    assert stored.vx == 0.0
    assert stored.vy == 0.0
    assert stored.yaw_rate == 0.0
    assert stored.source == "heartbeat"


@pytest.mark.asyncio
async def test_heartbeat_sends_zero_when_idle(heartbeat_env) -> None:
    controller, adapter, _, _ = heartbeat_env

    await controller.start()
    await asyncio.sleep(0.03)

    assert adapter.moves
    assert adapter.moves[0] == (0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_heartbeat_sends_latest_target_velocity(heartbeat_env) -> None:
    controller, adapter, _, _ = heartbeat_env

    await controller.set_target_velocity(0.2, -0.1, 0.4, source="nav")
    await controller.start()
    await asyncio.sleep(0.03)

    assert (0.2, -0.1, 0.4) in adapter.moves


@pytest.mark.asyncio
async def test_heartbeat_continues_after_move_failure(heartbeat_env) -> None:
    controller, adapter, _, _ = heartbeat_env
    adapter.fail_next_move = True

    await controller.start()
    await asyncio.sleep(0.04)

    assert controller.send_count() >= 2
    assert controller.last_send_ok() is True


@pytest.mark.asyncio
async def test_last_send_ok_and_send_count_update(heartbeat_env) -> None:
    controller, _, _, _ = heartbeat_env

    await controller.start()
    await asyncio.sleep(0.03)

    assert controller.send_count() >= 1
    assert controller.last_send_ok() is True


@pytest.mark.asyncio
async def test_estop_event_clears_target_and_calls_stop_motion(heartbeat_env) -> None:
    from core.event_bus import EventName

    controller, adapter, event_bus, _ = heartbeat_env
    await controller.set_target_velocity(0.3, 0.0, 0.1, source="nav")
    await controller.start()

    await event_bus.publish(EventName.ESTOP_TRIGGERED, {"reason": "test"})
    await event_bus.wait_until_idle(timeout=0.5)
    await asyncio.sleep(0.02)

    assert adapter.stop_motion_calls == 1
    cleared = await controller.get_target_velocity()
    assert cleared.vx == 0.0
    assert cleared.vy == 0.0
    assert cleared.yaw_rate == 0.0
    assert cleared.source == "estop"


def test_invalid_interval_rejected() -> None:
    from quadruped.heartbeat import HeartbeatError, HeartbeatController

    with pytest.raises(HeartbeatError):
        HeartbeatController(sdk_adapter=FakeAdapter(), interval_seconds=0)


def test_global_get_heartbeat_controller_returns_controller() -> None:
    from quadruped.heartbeat import HeartbeatController, get_heartbeat_controller

    assert isinstance(get_heartbeat_controller(), HeartbeatController)
