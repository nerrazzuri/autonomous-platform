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

    async def submit_task(self, **kwargs):
        if self.fail:
            raise RuntimeError("submit failed")
        self.submitted.append(kwargs)
        return FakeTask()


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
