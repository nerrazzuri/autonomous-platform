from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_state(*, battery_pct: int = 100, connection_ok: bool = True):
    from quadruped.sdk_adapter import QuadrupedMode
    from quadruped.state_monitor import QuadrupedState

    return QuadrupedState(
        timestamp=datetime.now(timezone.utc),
        battery_pct=battery_pct,
        position=(0.0, 0.0, 0.0),
        rpy=(0.0, 0.0, 0.0),
        control_mode=0,
        connection_ok=connection_ok,
        mode=QuadrupedMode.STANDING,
    )


class FakeStateMonitor:
    def __init__(self, state=None):
        self.state = state

    async def get_current_state(self):
        return self.state


class FakeDispatcher:
    def __init__(self, active_task_id=None):
        self._active_task_id = active_task_id

    async def get_state(self):
        from tasks.dispatcher import DispatchState

        return DispatchState(
            running=True,
            paused=False,
            active_task_id=self._active_task_id,
            active_route_origin=None,
            active_route_destination=None,
            last_result=None,
            loop_iteration=0,
        )


class FakeTaskQueue:
    def __init__(self):
        self.failed = []
        self.raise_on_fail = False

    async def mark_failed(self, task_id, notes=None):
        if self.raise_on_fail:
            raise RuntimeError("already terminal")
        self.failed.append((task_id, notes))

        class Task:
            id = task_id
            status = "failed"

        return Task()


@pytest_asyncio.fixture
async def watchdog_env(monkeypatch: pytest.MonkeyPatch):
    from core.event_bus import EventBus
    import tasks.watchdog as watchdog_module

    event_bus = EventBus()
    await event_bus.start()
    monkeypatch.setattr(watchdog_module, "get_event_bus", lambda: event_bus)
    yield watchdog_module, event_bus
    await event_bus.stop()


@pytest.mark.asyncio
async def test_start_and_stop_are_idempotent(watchdog_env) -> None:
    watchdog_module, _ = watchdog_env
    watchdog = watchdog_module.Watchdog(
        state_monitor=FakeStateMonitor(),
        dispatcher=FakeDispatcher(),
        task_queue=FakeTaskQueue(),
        loop_interval_seconds=0.01,
    )

    await watchdog.start()
    await watchdog.start()
    assert watchdog.is_running() is True

    await watchdog.stop()
    await watchdog.stop()
    assert watchdog.is_running() is False


def test_invalid_timeout_rejected() -> None:
    from tasks.watchdog import Watchdog, WatchdogError

    with pytest.raises(WatchdogError):
        Watchdog(
            state_monitor=FakeStateMonitor(),
            dispatcher=FakeDispatcher(),
            task_queue=FakeTaskQueue(),
            telemetry_timeout_seconds=0.0,
        )


def test_invalid_interval_rejected() -> None:
    from tasks.watchdog import Watchdog, WatchdogError

    with pytest.raises(WatchdogError):
        Watchdog(
            state_monitor=FakeStateMonitor(),
            dispatcher=FakeDispatcher(),
            task_queue=FakeTaskQueue(),
            loop_interval_seconds=0.0,
        )


@pytest.mark.asyncio
async def test_check_once_healthy_when_no_telemetry_yet(watchdog_env) -> None:
    watchdog_module, _ = watchdog_env
    watchdog = watchdog_module.Watchdog(
        state_monitor=FakeStateMonitor(),
        dispatcher=FakeDispatcher(),
        task_queue=FakeTaskQueue(),
    )

    healthy = await watchdog.check_once()

    assert healthy is True


@pytest.mark.asyncio
async def test_telemetry_event_updates_state(watchdog_env) -> None:
    watchdog_module, event_bus = watchdog_env
    watchdog = watchdog_module.Watchdog(
        state_monitor=FakeStateMonitor(),
        dispatcher=FakeDispatcher(),
        task_queue=FakeTaskQueue(),
    )
    await watchdog.start()

    await event_bus.publish("quadruped.telemetry", {"connection_ok": True, "battery_pct": 55})
    await event_bus.wait_until_idle(timeout=1.0)
    state = await watchdog.get_state()

    assert state.last_telemetry_at is not None
    assert state.last_connection_ok is True
    await watchdog.stop()


@pytest.mark.asyncio
async def test_telemetry_timeout_triggers_alert(watchdog_env) -> None:
    watchdog_module, _ = watchdog_env
    watchdog = watchdog_module.Watchdog(
        state_monitor=FakeStateMonitor(state=make_state()),
        dispatcher=FakeDispatcher(),
        task_queue=FakeTaskQueue(),
        telemetry_timeout_seconds=5.0,
    )
    watchdog._last_telemetry_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    watchdog._last_connection_ok = True

    healthy = await watchdog.check_once()
    state = await watchdog.get_state()

    assert healthy is False
    assert state.alert_active is True
    assert state.last_alert_reason == "telemetry_timeout"


@pytest.mark.asyncio
async def test_connection_lost_triggers_alert(watchdog_env) -> None:
    watchdog_module, _ = watchdog_env
    watchdog = watchdog_module.Watchdog(
        state_monitor=FakeStateMonitor(state=make_state(connection_ok=False)),
        dispatcher=FakeDispatcher(),
        task_queue=FakeTaskQueue(),
    )
    watchdog._last_telemetry_at = datetime.now(timezone.utc)
    watchdog._last_connection_ok = False

    healthy = await watchdog.check_once()
    state = await watchdog.get_state()

    assert healthy is False
    assert state.alert_active is True
    assert state.last_alert_reason == "connection_lost"


@pytest.mark.asyncio
async def test_power_loss_heuristic_reason(watchdog_env) -> None:
    watchdog_module, _ = watchdog_env
    watchdog = watchdog_module.Watchdog(
        state_monitor=FakeStateMonitor(state=make_state(battery_pct=0, connection_ok=False)),
        dispatcher=FakeDispatcher(),
        task_queue=FakeTaskQueue(),
    )
    watchdog._last_telemetry_at = datetime.now(timezone.utc)
    watchdog._last_connection_ok = False

    await watchdog.check_once()
    state = await watchdog.get_state()

    assert state.last_alert_reason == "quadruped_power_loss"


@pytest.mark.asyncio
async def test_active_task_marked_failed_on_interruption(watchdog_env) -> None:
    watchdog_module, _ = watchdog_env
    queue = FakeTaskQueue()
    watchdog = watchdog_module.Watchdog(
        state_monitor=FakeStateMonitor(state=make_state(connection_ok=False)),
        dispatcher=FakeDispatcher(active_task_id="task-1"),
        task_queue=queue,
    )
    watchdog._last_telemetry_at = datetime.now(timezone.utc)
    watchdog._last_connection_ok = False

    await watchdog.check_once()

    assert queue.failed == [("task-1", "connection_lost")]


@pytest.mark.asyncio
async def test_no_active_task_does_not_fail_task(watchdog_env) -> None:
    watchdog_module, _ = watchdog_env
    queue = FakeTaskQueue()
    watchdog = watchdog_module.Watchdog(
        state_monitor=FakeStateMonitor(state=make_state(connection_ok=False)),
        dispatcher=FakeDispatcher(active_task_id=None),
        task_queue=queue,
    )
    watchdog._last_telemetry_at = datetime.now(timezone.utc)
    watchdog._last_connection_ok = False

    await watchdog.check_once()

    assert queue.failed == []


@pytest.mark.asyncio
async def test_repeated_checks_do_not_duplicate_failure_handling(watchdog_env) -> None:
    watchdog_module, _ = watchdog_env
    queue = FakeTaskQueue()
    watchdog = watchdog_module.Watchdog(
        state_monitor=FakeStateMonitor(state=make_state(connection_ok=False)),
        dispatcher=FakeDispatcher(active_task_id="task-1"),
        task_queue=queue,
    )
    watchdog._last_telemetry_at = datetime.now(timezone.utc)
    watchdog._last_connection_ok = False

    await watchdog.check_once()
    await watchdog.check_once()

    assert queue.failed == [("task-1", "connection_lost")]


@pytest.mark.asyncio
async def test_recovery_clears_alert_state(watchdog_env) -> None:
    watchdog_module, event_bus = watchdog_env
    watchdog = watchdog_module.Watchdog(
        state_monitor=FakeStateMonitor(state=make_state(connection_ok=True)),
        dispatcher=FakeDispatcher(active_task_id=None),
        task_queue=FakeTaskQueue(),
    )
    watchdog._alert_active = True
    watchdog._last_alert_reason = "connection_lost"
    watchdog._last_connection_ok = False
    await watchdog.start()

    await event_bus.publish("quadruped.telemetry", {"connection_ok": True})
    await event_bus.wait_until_idle(timeout=1.0)
    state = await watchdog.get_state()

    assert state.alert_active is False
    assert state.last_alert_reason is None
    await watchdog.stop()


def test_global_get_watchdog_returns_watchdog() -> None:
    from tasks.watchdog import Watchdog, get_watchdog

    assert isinstance(get_watchdog(), Watchdog)
