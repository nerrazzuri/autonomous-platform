from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeTask:
    def __init__(self, task_id: str = "dock-task-1"):
        self.id = task_id


class FakeTaskQueue:
    def __init__(self):
        self.submitted = []
        self.fail = False
        self.counter = 0

    async def submit_task(self, **kwargs):
        if self.fail:
            raise RuntimeError("submit failed")
        self.counter += 1
        self.submitted.append(kwargs)
        return FakeTask(kwargs.get("task_id", f"dock-task-{self.counter}"))


class FakeDispatcher:
    def __init__(self):
        self.paused = False
        self.pause_calls = 0
        self.resume_calls = 0
        self.pause_fail = False
        self.resume_fail = False

    async def pause(self, reason="paused"):
        if self.pause_fail:
            raise RuntimeError("pause failed")
        self.paused = True
        self.pause_calls += 1

    async def resume(self):
        if self.resume_fail:
            raise RuntimeError("resume failed")
        self.paused = False
        self.resume_calls += 1


class FakeStateMonitor:
    pass


class FakeRobotRegistry:
    def __init__(self, platforms: list[object] | None = None):
        self._platforms = {platform.robot_id: platform for platform in platforms or []}

    def get(self, robot_id: str):
        from shared.quadruped.robot_registry import RobotNotFoundError

        try:
            return self._platforms[robot_id]
        except KeyError as exc:
            raise RobotNotFoundError(f"Robot '{robot_id}' is not registered") from exc

    def all(self):
        return list(self._platforms.values())


def make_robot_platform(robot_id: str):
    return type(
        "FakePlatform",
        (),
        {
            "robot_id": robot_id,
            "config": type("FakeConfig", (), {"connection": type("FakeConnection", (), {"robot_id": robot_id})()})(),
        },
    )()


@pytest_asyncio.fixture
async def battery_manager_env(monkeypatch: pytest.MonkeyPatch):
    from core.event_bus import EventBus
    import tasks.battery_manager as battery_manager_module

    event_bus = EventBus()
    await event_bus.start()
    monkeypatch.setattr(battery_manager_module, "get_event_bus", lambda: event_bus)
    yield battery_manager_module, event_bus
    await event_bus.stop()


@pytest.mark.asyncio
async def test_start_and_stop_are_idempotent(battery_manager_env) -> None:
    battery_manager_module, _ = battery_manager_env
    manager = battery_manager_module.BatteryManager(
        task_queue=FakeTaskQueue(),
        dispatcher=FakeDispatcher(),
        state_monitor=FakeStateMonitor(),
    )

    await manager.start()
    await manager.start()
    assert manager.is_running() is True

    await manager.stop()
    await manager.stop()
    assert manager.is_running() is False


def test_invalid_charging_poll_seconds_rejected() -> None:
    from tasks.battery_manager import BatteryManager, BatteryManagerError

    with pytest.raises(BatteryManagerError):
        BatteryManager(
            task_queue=FakeTaskQueue(),
            dispatcher=FakeDispatcher(),
            state_monitor=FakeStateMonitor(),
            charging_poll_seconds=0,
        )


@pytest.mark.asyncio
async def test_battery_warn_updates_state_without_charging_mode(battery_manager_env) -> None:
    battery_manager_module, _ = battery_manager_env
    manager = battery_manager_module.BatteryManager(
        task_queue=FakeTaskQueue(),
        dispatcher=FakeDispatcher(),
        state_monitor=FakeStateMonitor(),
    )

    await manager.handle_battery_warn(29)
    state = await manager.get_state()

    assert state.last_battery_pct == 29
    assert state.charging_mode is False


@pytest.mark.asyncio
async def test_battery_critical_enters_charging_mode_and_creates_dock_task(battery_manager_env) -> None:
    battery_manager_module, _ = battery_manager_env
    queue = FakeTaskQueue()
    dispatcher = FakeDispatcher()
    manager = battery_manager_module.BatteryManager(
        task_queue=queue,
        dispatcher=dispatcher,
        state_monitor=FakeStateMonitor(),
    )

    await manager.handle_battery_critical(20)
    state = await manager.get_state()

    assert state.charging_mode is True
    assert state.dock_task_id == "dock-task-1"
    assert state.dock_task_active is True
    assert queue.submitted[0]["station_id"] == "CURRENT"
    assert queue.submitted[0]["destination_id"] == "DOCK"
    assert queue.submitted[0]["priority"] == 9999


@pytest.mark.asyncio
async def test_battery_critical_does_not_create_duplicate_dock_task(battery_manager_env) -> None:
    battery_manager_module, _ = battery_manager_env
    queue = FakeTaskQueue()
    manager = battery_manager_module.BatteryManager(
        task_queue=queue,
        dispatcher=FakeDispatcher(),
        state_monitor=FakeStateMonitor(),
    )

    await manager.handle_battery_critical(20)
    await manager.handle_battery_critical(19)

    assert len(queue.submitted) == 1


@pytest.mark.asyncio
async def test_battery_critical_for_robot_01_only_affects_robot_01(battery_manager_env) -> None:
    battery_manager_module, _ = battery_manager_env
    queue = FakeTaskQueue()
    registry = FakeRobotRegistry([make_robot_platform("robot_01"), make_robot_platform("robot_02")])
    manager = battery_manager_module.BatteryManager(
        task_queue=queue,
        dispatcher=FakeDispatcher(),
        state_monitor=FakeStateMonitor(),
        robot_registry=registry,
    )

    await manager.handle_battery_critical(20, robot_id="robot_01")

    assert manager._charging_mode["robot_01"] is True
    assert manager._dock_task_active["robot_01"] is True
    assert manager._charging_mode.get("robot_02", False) is False
    assert manager._dock_task_active.get("robot_02", False) is False
    assert len(queue.submitted) == 1
    assert "robot_01" in queue.submitted[0]["task_id"]


@pytest.mark.asyncio
async def test_battery_critical_for_two_robots_is_independent(battery_manager_env) -> None:
    battery_manager_module, _ = battery_manager_env
    queue = FakeTaskQueue()
    registry = FakeRobotRegistry([make_robot_platform("robot_01"), make_robot_platform("robot_02")])
    manager = battery_manager_module.BatteryManager(
        task_queue=queue,
        dispatcher=FakeDispatcher(),
        state_monitor=FakeStateMonitor(),
        robot_registry=registry,
    )

    await manager.handle_battery_critical(20, robot_id="robot_01")
    await manager.handle_battery_critical(19, robot_id="robot_02")

    assert manager._charging_mode == {"robot_01": True, "robot_02": True}
    assert manager._dock_task_active == {"robot_01": True, "robot_02": True}
    assert len(queue.submitted) == 2
    assert "robot_01" in queue.submitted[0]["task_id"]
    assert "robot_02" in queue.submitted[1]["task_id"]


@pytest.mark.asyncio
async def test_duplicate_battery_critical_for_same_robot_does_not_duplicate_dock_task(battery_manager_env) -> None:
    battery_manager_module, _ = battery_manager_env
    queue = FakeTaskQueue()
    registry = FakeRobotRegistry([make_robot_platform("robot_01"), make_robot_platform("robot_02")])
    manager = battery_manager_module.BatteryManager(
        task_queue=queue,
        dispatcher=FakeDispatcher(),
        state_monitor=FakeStateMonitor(),
        robot_registry=registry,
    )

    await manager.handle_battery_critical(20, robot_id="robot_01")
    await manager.handle_battery_critical(19, robot_id="robot_01")

    assert len(queue.submitted) == 1
    assert list(manager._dock_task_id) == ["robot_01"]


@pytest.mark.asyncio
async def test_battery_recharged_for_robot_01_does_not_clear_robot_02(battery_manager_env) -> None:
    battery_manager_module, _ = battery_manager_env
    queue = FakeTaskQueue()
    registry = FakeRobotRegistry([make_robot_platform("robot_01"), make_robot_platform("robot_02")])
    manager = battery_manager_module.BatteryManager(
        task_queue=queue,
        dispatcher=FakeDispatcher(),
        state_monitor=FakeStateMonitor(),
        robot_registry=registry,
    )

    await manager.handle_battery_critical(20, robot_id="robot_01")
    await manager.handle_battery_critical(19, robot_id="robot_02")
    await manager.handle_battery_recharged(95, robot_id="robot_01")

    assert manager._charging_mode.get("robot_01", False) is False
    assert manager._dock_task_active.get("robot_01", False) is False
    assert manager._dock_task_id.get("robot_01") is None
    assert manager._charging_mode["robot_02"] is True
    assert manager._dock_task_active["robot_02"] is True
    assert manager._dock_task_id["robot_02"] is not None


@pytest.mark.asyncio
async def test_unknown_robot_id_event_is_ignored_safely(battery_manager_env) -> None:
    battery_manager_module, event_bus = battery_manager_env
    queue = FakeTaskQueue()
    registry = FakeRobotRegistry([make_robot_platform("robot_01")])
    manager = battery_manager_module.BatteryManager(
        task_queue=queue,
        dispatcher=FakeDispatcher(),
        state_monitor=FakeStateMonitor(),
        robot_registry=registry,
    )

    await manager.start()
    await manager.handle_battery_critical(20, robot_id="robot_01")
    await event_bus.publish("battery.critical", {"robot_id": "robot_999", "battery_pct": 18})
    await event_bus.wait_until_idle(timeout=1.0)

    assert manager._charging_mode["robot_01"] is True
    assert len(queue.submitted) == 1

    await manager.stop()


@pytest.mark.asyncio
async def test_legacy_no_robot_id_event_still_works(battery_manager_env) -> None:
    battery_manager_module, event_bus = battery_manager_env
    queue = FakeTaskQueue()
    manager = battery_manager_module.BatteryManager(
        task_queue=queue,
        dispatcher=FakeDispatcher(),
        state_monitor=FakeStateMonitor(),
        robot_registry=FakeRobotRegistry(),
    )

    await manager.start()
    await event_bus.publish("battery.critical", {"battery_pct": 20})
    await event_bus.wait_until_idle(timeout=1.0)

    assert manager._charging_mode["default"] is True
    assert manager._dock_task_active["default"] is True
    assert len(queue.submitted) == 1

    await manager.stop()


@pytest.mark.asyncio
async def test_battery_critical_pauses_then_resumes_dispatcher_for_dock_flow(battery_manager_env) -> None:
    battery_manager_module, _ = battery_manager_env
    dispatcher = FakeDispatcher()
    manager = battery_manager_module.BatteryManager(
        task_queue=FakeTaskQueue(),
        dispatcher=dispatcher,
        state_monitor=FakeStateMonitor(),
    )

    await manager.handle_battery_critical(20)

    assert dispatcher.pause_calls == 1
    assert dispatcher.resume_calls == 1


@pytest.mark.asyncio
async def test_battery_recharged_exits_charging_mode(battery_manager_env) -> None:
    battery_manager_module, _ = battery_manager_env
    dispatcher = FakeDispatcher()
    manager = battery_manager_module.BatteryManager(
        task_queue=FakeTaskQueue(),
        dispatcher=dispatcher,
        state_monitor=FakeStateMonitor(),
    )
    await manager.handle_battery_critical(20)

    await manager.handle_battery_recharged(95)
    state = await manager.get_state()

    assert state.charging_mode is False
    assert state.dock_task_id is None
    assert state.dock_task_active is False
    assert dispatcher.resume_calls >= 2


@pytest.mark.asyncio
async def test_battery_recharged_when_not_in_charging_mode_is_safe(battery_manager_env) -> None:
    battery_manager_module, _ = battery_manager_env
    manager = battery_manager_module.BatteryManager(
        task_queue=FakeTaskQueue(),
        dispatcher=FakeDispatcher(),
        state_monitor=FakeStateMonitor(),
    )

    await manager.handle_battery_recharged(95)
    state = await manager.get_state()

    assert state.charging_mode is False
    assert state.last_battery_pct == 95


@pytest.mark.asyncio
async def test_direct_handler_calls_work_without_start(battery_manager_env) -> None:
    battery_manager_module, _ = battery_manager_env
    manager = battery_manager_module.BatteryManager(
        task_queue=FakeTaskQueue(),
        dispatcher=FakeDispatcher(),
        state_monitor=FakeStateMonitor(),
    )

    await manager.handle_battery_critical(20)
    state = await manager.get_state()

    assert state.charging_mode is True
    assert manager.is_running() is False


@pytest.mark.asyncio
async def test_submit_task_failure_on_critical_sets_error_state(battery_manager_env) -> None:
    battery_manager_module, _ = battery_manager_env
    queue = FakeTaskQueue()
    queue.fail = True
    dispatcher = FakeDispatcher()
    manager = battery_manager_module.BatteryManager(
        task_queue=queue,
        dispatcher=dispatcher,
        state_monitor=FakeStateMonitor(),
    )

    await manager.handle_battery_critical(20)
    state = await manager.get_state()

    assert state.charging_mode is True
    assert state.dock_task_id is None
    assert manager.last_error() is not None
    assert "submit failed" in manager.last_error()
    assert dispatcher.paused is True


def test_global_get_battery_manager_returns_manager() -> None:
    from tasks.battery_manager import BatteryManager, get_battery_manager

    assert isinstance(get_battery_manager(), BatteryManager)
