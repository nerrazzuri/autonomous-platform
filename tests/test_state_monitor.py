from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quadruped.sdk_adapter import QuadrupedMode, QuadrupedTelemetrySnapshot


class FakeSDKAdapter:
    def __init__(self):
        self.snapshot = QuadrupedTelemetrySnapshot(
            battery_pct=100,
            position=(1.0, 2.0, 0.0),
            rpy=(0.0, 0.0, 0.1),
            control_mode=0,
            connection_ok=True,
            mode=QuadrupedMode.STANDING,
        )
        self.fail_next = False

    async def get_telemetry_snapshot(self):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("snapshot failed")
        return self.snapshot


class FakeDatabase:
    def __init__(self):
        self.initialized = False
        self.telemetry = []
        self.fail_write = False

    async def initialize(self):
        self.initialized = True

    async def log_telemetry(self, **kwargs):
        if self.fail_write:
            raise RuntimeError("db write failed")
        self.telemetry.append(kwargs)


@pytest_asyncio.fixture
async def monitor_env(monkeypatch: pytest.MonkeyPatch):
    from core.event_bus import EventBus
    import quadruped.state_monitor as state_monitor_module

    event_bus = EventBus()
    await event_bus.start()
    monkeypatch.setattr(state_monitor_module, "get_event_bus", lambda: event_bus)

    sdk_adapter = FakeSDKAdapter()
    database = FakeDatabase()
    monitor = state_monitor_module.StateMonitor(
        sdk_adapter=sdk_adapter,
        database=database,
        poll_interval_seconds=0.01,
        persist_telemetry=True,
        robot_id="robot-1",
    )
    yield monitor, sdk_adapter, database, event_bus, state_monitor_module
    await monitor.stop()
    await event_bus.stop()


def test_quadruped_state_to_dict() -> None:
    from quadruped.state_monitor import QuadrupedState

    state = QuadrupedState(
        timestamp=datetime.now(timezone.utc),
        battery_pct=95,
        position=(1.0, 2.0, 0.0),
        rpy=(0.0, 0.0, 0.1),
        control_mode=1,
        connection_ok=True,
        mode=QuadrupedMode.STANDING,
    )

    payload = state.to_dict()

    assert isinstance(payload["timestamp"], str)
    assert payload["battery_pct"] == 95
    assert payload["position"] == [1.0, 2.0, 0.0]
    assert payload["rpy"] == [0.0, 0.0, 0.1]
    assert payload["mode"] == "standing"


def test_battery_threshold_helpers() -> None:
    from quadruped.state_monitor import QuadrupedState

    state = QuadrupedState(
        timestamp=datetime.now(timezone.utc),
        battery_pct=25,
        position=(0.0, 0.0, 0.0),
        rpy=(0.0, 0.0, 0.0),
        control_mode=0,
        connection_ok=True,
        mode=QuadrupedMode.STANDING,
    )

    assert state.is_battery_warn(30) is True
    assert state.is_battery_critical(25) is True


@pytest.mark.asyncio
async def test_initial_current_state_is_none(monitor_env) -> None:
    monitor, _, _, _, _ = monitor_env

    assert await monitor.get_current_state() is None


def test_invalid_poll_interval_rejected() -> None:
    from quadruped.state_monitor import StateMonitor, StateMonitorError

    with pytest.raises(StateMonitorError):
        StateMonitor(sdk_adapter=FakeSDKAdapter(), database=FakeDatabase(), poll_interval_seconds=0)


def test_constructor_accepts_robot_id() -> None:
    from quadruped.state_monitor import StateMonitor

    monitor = StateMonitor(
        sdk_adapter=FakeSDKAdapter(),
        database=FakeDatabase(),
        poll_interval_seconds=0.01,
        robot_id="robot-7",
    )

    assert monitor.robot_id == "robot-7"


def test_constructor_rejects_empty_robot_id() -> None:
    from quadruped.state_monitor import StateMonitor, StateMonitorError

    with pytest.raises(StateMonitorError, match="robot_id"):
        StateMonitor(
            sdk_adapter=FakeSDKAdapter(),
            database=FakeDatabase(),
            poll_interval_seconds=0.01,
            robot_id="",
        )


def test_default_robot_id_is_default() -> None:
    from quadruped.state_monitor import StateMonitor

    monitor = StateMonitor(
        sdk_adapter=FakeSDKAdapter(),
        database=FakeDatabase(),
        poll_interval_seconds=0.01,
    )

    assert monitor.robot_id == "default"


@pytest.mark.asyncio
async def test_poll_once_updates_current_state(monitor_env) -> None:
    monitor, _, _, _, _ = monitor_env

    state = await monitor.poll_once()
    current = await monitor.get_current_state()

    assert current == state
    assert state.connection_ok is True
    assert state.mode == QuadrupedMode.STANDING


@pytest.mark.asyncio
async def test_poll_once_publishes_telemetry_event(monitor_env) -> None:
    from core.event_bus import EventName

    monitor, _, _, event_bus, _ = monitor_env
    received = []

    async def callback(event):
        received.append(event.payload)

    event_bus.subscribe(EventName.QUADRUPED_TELEMETRY, callback)
    await monitor.poll_once()
    await event_bus.wait_until_idle(timeout=0.5)

    assert len(received) == 1
    assert received[0]["battery_pct"] == 100
    assert received[0]["robot_id"] == "robot-1"


@pytest.mark.asyncio
async def test_poll_once_persists_telemetry_when_enabled(monitor_env) -> None:
    monitor, _, database, _, _ = monitor_env

    await monitor.poll_once()

    assert len(database.telemetry) == 1
    assert database.telemetry[0]["battery_pct"] == 100


@pytest.mark.asyncio
async def test_poll_once_does_not_persist_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.event_bus import EventBus
    import quadruped.state_monitor as state_monitor_module

    event_bus = EventBus()
    await event_bus.start()
    monkeypatch.setattr(state_monitor_module, "get_event_bus", lambda: event_bus)

    sdk_adapter = FakeSDKAdapter()
    database = FakeDatabase()
    monitor = state_monitor_module.StateMonitor(
        sdk_adapter=sdk_adapter,
        database=database,
        poll_interval_seconds=0.01,
        persist_telemetry=False,
    )
    try:
        await monitor.poll_once()
        assert database.telemetry == []
    finally:
        await monitor.stop()
        await event_bus.stop()


@pytest.mark.asyncio
async def test_connection_lost_and_restored_events(monitor_env) -> None:
    from core.event_bus import EventName

    monitor, sdk_adapter, _, event_bus, _ = monitor_env
    received = []

    async def callback(event):
        received.append((event.name, event.payload))

    event_bus.subscribe(EventName.QUADRUPED_CONNECTION_LOST, callback)
    event_bus.subscribe(EventName.QUADRUPED_CONNECTION_RESTORED, callback)

    await monitor.poll_once()
    sdk_adapter.snapshot = QuadrupedTelemetrySnapshot(
        battery_pct=100,
        position=(1.0, 2.0, 0.0),
        rpy=(0.0, 0.0, 0.1),
        control_mode=0,
        connection_ok=False,
        mode=QuadrupedMode.ERROR,
    )
    await monitor.poll_once()
    sdk_adapter.snapshot = QuadrupedTelemetrySnapshot(
        battery_pct=100,
        position=(1.0, 2.0, 0.0),
        rpy=(0.0, 0.0, 0.1),
        control_mode=0,
        connection_ok=True,
        mode=QuadrupedMode.STANDING,
    )
    await monitor.poll_once()
    await event_bus.wait_until_idle(timeout=0.5)

    assert [event_name for event_name, _ in received] == [
        EventName.QUADRUPED_CONNECTION_RESTORED,
        EventName.QUADRUPED_CONNECTION_LOST,
        EventName.QUADRUPED_CONNECTION_RESTORED,
    ]
    assert all(payload["robot_id"] == "robot-1" for _, payload in received)


@pytest.mark.asyncio
async def test_battery_warn_emitted_once(monitor_env) -> None:
    from core.event_bus import EventName

    monitor, sdk_adapter, _, event_bus, _ = monitor_env
    received = []

    async def callback(event):
        received.append(event.name)

    event_bus.subscribe(EventName.BATTERY_WARN, callback)
    sdk_adapter.snapshot = QuadrupedTelemetrySnapshot(
        battery_pct=30,
        position=(1.0, 2.0, 0.0),
        rpy=(0.0, 0.0, 0.1),
        control_mode=0,
        connection_ok=True,
        mode=QuadrupedMode.STANDING,
    )
    await monitor.poll_once()
    await monitor.poll_once()
    await event_bus.wait_until_idle(timeout=0.5)

    assert received == [EventName.BATTERY_WARN]


@pytest.mark.asyncio
async def test_battery_critical_emitted_once(monitor_env) -> None:
    from core.event_bus import EventName

    monitor, sdk_adapter, _, event_bus, _ = monitor_env
    received = []

    async def callback(event):
        received.append(event.name)

    event_bus.subscribe(EventName.BATTERY_CRITICAL, callback)
    sdk_adapter.snapshot = QuadrupedTelemetrySnapshot(
        battery_pct=20,
        position=(1.0, 2.0, 0.0),
        rpy=(0.0, 0.0, 0.1),
        control_mode=0,
        connection_ok=True,
        mode=QuadrupedMode.STANDING,
    )
    await monitor.poll_once()
    await monitor.poll_once()
    await event_bus.wait_until_idle(timeout=0.5)

    assert received == [EventName.BATTERY_CRITICAL]


@pytest.mark.asyncio
async def test_battery_recharged_resets_flags(monitor_env) -> None:
    from core.event_bus import EventName

    monitor, sdk_adapter, _, event_bus, _ = monitor_env
    received = []

    async def callback(event):
        received.append(event.name)

    event_bus.subscribe(EventName.BATTERY_WARN, callback)
    event_bus.subscribe(EventName.BATTERY_RECHARGED, callback)

    sdk_adapter.snapshot = QuadrupedTelemetrySnapshot(
        battery_pct=25,
        position=(1.0, 2.0, 0.0),
        rpy=(0.0, 0.0, 0.1),
        control_mode=0,
        connection_ok=True,
        mode=QuadrupedMode.STANDING,
    )
    await monitor.poll_once()
    sdk_adapter.snapshot = QuadrupedTelemetrySnapshot(
        battery_pct=95,
        position=(1.0, 2.0, 0.0),
        rpy=(0.0, 0.0, 0.1),
        control_mode=0,
        connection_ok=True,
        mode=QuadrupedMode.STANDING,
    )
    await monitor.poll_once()
    sdk_adapter.snapshot = QuadrupedTelemetrySnapshot(
        battery_pct=25,
        position=(1.0, 2.0, 0.0),
        rpy=(0.0, 0.0, 0.1),
        control_mode=0,
        connection_ok=True,
        mode=QuadrupedMode.STANDING,
    )
    await monitor.poll_once()
    await event_bus.wait_until_idle(timeout=0.5)

    assert received == [
        EventName.BATTERY_WARN,
        EventName.BATTERY_RECHARGED,
        EventName.BATTERY_WARN,
    ]


@pytest.mark.asyncio
async def test_battery_events_include_robot_id(monitor_env) -> None:
    from core.event_bus import EventName

    monitor, sdk_adapter, _, event_bus, _ = monitor_env
    received = []

    async def callback(event):
        received.append((event.name, event.payload))

    event_bus.subscribe(EventName.BATTERY_WARN, callback)
    event_bus.subscribe(EventName.BATTERY_CRITICAL, callback)
    event_bus.subscribe(EventName.BATTERY_RECHARGED, callback)

    sdk_adapter.snapshot = QuadrupedTelemetrySnapshot(
        battery_pct=20,
        position=(1.0, 2.0, 0.0),
        rpy=(0.0, 0.0, 0.1),
        control_mode=0,
        connection_ok=True,
        mode=QuadrupedMode.STANDING,
    )
    await monitor.poll_once()
    sdk_adapter.snapshot = QuadrupedTelemetrySnapshot(
        battery_pct=95,
        position=(1.0, 2.0, 0.0),
        rpy=(0.0, 0.0, 0.1),
        control_mode=0,
        connection_ok=True,
        mode=QuadrupedMode.STANDING,
    )
    await monitor.poll_once()
    await event_bus.wait_until_idle(timeout=0.5)

    assert [event_name for event_name, _ in received] == [
        EventName.BATTERY_WARN,
        EventName.BATTERY_CRITICAL,
        EventName.BATTERY_RECHARGED,
    ]
    assert all(payload["robot_id"] == "robot-1" for _, payload in received)


@pytest.mark.asyncio
async def test_start_and_stop_are_idempotent(monitor_env) -> None:
    monitor, _, database, _, _ = monitor_env

    await monitor.start()
    assert monitor.is_running() is True
    first_task = monitor._task
    assert database.initialized is True

    await monitor.start()
    assert monitor._task is first_task

    await monitor.stop()
    await monitor.stop()
    assert monitor.is_running() is False


@pytest.mark.asyncio
async def test_stop_before_start_does_not_crash(monitor_env) -> None:
    monitor, _, _, _, _ = monitor_env

    await monitor.stop()

    assert monitor.is_running() is False


@pytest.mark.asyncio
async def test_poll_loop_continues_after_failure(monitor_env) -> None:
    monitor, sdk_adapter, _, _, _ = monitor_env
    sdk_adapter.fail_next = True

    await monitor.start()
    await asyncio.sleep(0.04)

    assert monitor.poll_count() >= 2
    assert await monitor.get_current_state() is not None


@pytest.mark.asyncio
async def test_database_write_failure_does_not_crash_poll(monitor_env) -> None:
    monitor, _, database, _, _ = monitor_env
    database.fail_write = True

    state = await monitor.poll_once()

    assert state.connection_ok is True
    assert await monitor.get_current_state() is not None


def test_global_get_state_monitor_returns_monitor() -> None:
    from quadruped.state_monitor import StateMonitor, get_state_monitor

    assert isinstance(get_state_monitor(), StateMonitor)
