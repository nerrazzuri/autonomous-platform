from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakePatrolQueue:
    def __init__(self) -> None:
        self.status = {"scheduled": 0, "active": 0}
        self.raise_on_status = False

    async def get_queue_status(self) -> dict[str, int]:
        if self.raise_on_status:
            raise RuntimeError("queue status failed")
        return dict(self.status)


class FakeEventBus:
    def __init__(self) -> None:
        self._subscriptions: dict[str, tuple[object, object]] = {}
        self._published = []
        self._counter = 0

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def subscribe(self, event_name, callback, *, subscriber_name: str | None = None) -> str:
        self._counter += 1
        subscription_id = f"sub-{self._counter}"
        self._subscriptions[subscription_id] = (event_name, callback)
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        return self._subscriptions.pop(subscription_id, None) is not None

    async def publish(self, event_name, payload: dict[str, object] | None = None, *, task_id: str | None = None, **_kwargs):
        event = SimpleNamespace(name=event_name, payload=dict(payload or {}), task_id=task_id)
        self._published.append(event)
        for subscribed_name, callback in list(self._subscriptions.values()):
            if subscribed_name == event_name:
                result = callback(event)
                if asyncio.iscoroutine(result):
                    await result
        return event

    def subscriber_count(self, event_name=None) -> int:
        if event_name is None:
            return len(self._subscriptions)
        return sum(1 for subscribed_name, _callback in self._subscriptions.values() if subscribed_name == event_name)

    @property
    def published(self):
        return list(self._published)


@pytest.fixture
def watchdog_module():
    return importlib.import_module("apps.patrol.tasks.patrol_watchdog")


def build_watchdog(watchdog_module, **kwargs):
    return watchdog_module.PatrolWatchdog(
        patrol_queue=kwargs.pop("patrol_queue", FakePatrolQueue()),
        event_bus=kwargs.pop("event_bus", FakeEventBus()),
        patrol_interval_seconds=kwargs.pop("patrol_interval_seconds", 10.0),
        loop_interval_seconds=kwargs.pop("loop_interval_seconds", 60.0),
    )


def test_invalid_intervals_rejected(watchdog_module) -> None:
    with pytest.raises(watchdog_module.PatrolWatchdogError, match="patrol_interval_seconds"):
        build_watchdog(watchdog_module, patrol_interval_seconds=0)

    with pytest.raises(watchdog_module.PatrolWatchdogError, match="loop_interval_seconds"):
        build_watchdog(watchdog_module, loop_interval_seconds=0)


@pytest.mark.asyncio
async def test_check_once_healthy_initially(watchdog_module) -> None:
    watchdog = build_watchdog(watchdog_module)

    assert await watchdog.check_once() is True
    assert (await watchdog.get_state()).last_alert_reason is None


@pytest.mark.asyncio
async def test_cycle_completed_event_updates_timestamp(watchdog_module) -> None:
    event_bus = FakeEventBus()
    watchdog = build_watchdog(watchdog_module, event_bus=event_bus)

    await watchdog.start()
    try:
        await event_bus.publish(watchdog_module.EventName.PATROL_CYCLE_COMPLETED, {"cycle_id": "cycle-1"})
    finally:
        await watchdog.stop()

    assert (await watchdog.get_state()).last_cycle_completed_at is not None


@pytest.mark.asyncio
async def test_stall_alert_after_3x_interval(watchdog_module) -> None:
    event_bus = FakeEventBus()
    watchdog = build_watchdog(watchdog_module, event_bus=event_bus, patrol_interval_seconds=10.0)
    watchdog._last_cycle_completed_at = datetime.now(UTC) - timedelta(seconds=31)

    healthy = await watchdog.check_once()
    state = await watchdog.get_state()

    assert healthy is False
    assert state.last_alert_reason == "patrol_stalled"
    assert event_bus.published[-1].payload["reason"] == "patrol_stalled"


@pytest.mark.asyncio
async def test_no_stall_alert_when_suspended(watchdog_module) -> None:
    watchdog = build_watchdog(watchdog_module)
    watchdog._suspended = True
    watchdog._last_cycle_completed_at = datetime.now(UTC) - timedelta(seconds=100)

    assert await watchdog.check_once() is True


@pytest.mark.asyncio
async def test_missed_cycle_alert_when_scheduled_accumulates(watchdog_module) -> None:
    queue = FakePatrolQueue()
    queue.status = {"scheduled": 2, "active": 0}
    event_bus = FakeEventBus()
    watchdog = build_watchdog(watchdog_module, patrol_queue=queue, event_bus=event_bus)

    healthy = await watchdog.check_once()
    state = await watchdog.get_state()

    assert healthy is False
    assert state.last_alert_reason == "patrol_cycles_accumulating"
    assert event_bus.published[-1].payload["reason"] == "patrol_cycles_accumulating"


@pytest.mark.asyncio
async def test_duplicate_alert_suppression(watchdog_module) -> None:
    queue = FakePatrolQueue()
    queue.status = {"scheduled": 3, "active": 0}
    event_bus = FakeEventBus()
    watchdog = build_watchdog(watchdog_module, patrol_queue=queue, event_bus=event_bus)

    assert await watchdog.check_once() is False
    assert await watchdog.check_once() is False

    alerts = [event for event in event_bus.published if event.name == watchdog_module.EventName.SYSTEM_ALERT]
    assert len(alerts) == 1


@pytest.mark.asyncio
async def test_patrol_resumed_clears_suspended(watchdog_module) -> None:
    event_bus = FakeEventBus()
    watchdog = build_watchdog(watchdog_module, event_bus=event_bus)

    await watchdog.start()
    try:
        await event_bus.publish(watchdog_module.EventName.PATROL_SUSPENDED, {})
        assert (await watchdog.get_state()).suspended is True
        await event_bus.publish(watchdog_module.EventName.PATROL_RESUMED, {})
        assert (await watchdog.get_state()).suspended is False
    finally:
        await watchdog.stop()


@pytest.mark.asyncio
async def test_start_and_stop_are_idempotent(watchdog_module) -> None:
    event_bus = FakeEventBus()
    watchdog = build_watchdog(watchdog_module, event_bus=event_bus)

    await watchdog.start()
    await watchdog.start()

    assert watchdog.is_running() is True
    assert event_bus.subscriber_count(watchdog_module.EventName.PATROL_CYCLE_COMPLETED) == 1
    assert event_bus.subscriber_count(watchdog_module.EventName.PATROL_SUSPENDED) == 1
    assert event_bus.subscriber_count(watchdog_module.EventName.PATROL_RESUMED) == 1

    await watchdog.stop()
    await watchdog.stop()

    assert watchdog.is_running() is False
    assert event_bus.subscriber_count(watchdog_module.EventName.PATROL_CYCLE_COMPLETED) == 0
    assert event_bus.subscriber_count(watchdog_module.EventName.PATROL_SUSPENDED) == 0
    assert event_bus.subscriber_count(watchdog_module.EventName.PATROL_RESUMED) == 0


@pytest.mark.asyncio
async def test_loop_iteration_increments(watchdog_module) -> None:
    watchdog = build_watchdog(watchdog_module, loop_interval_seconds=0.01)

    await watchdog.start()
    await asyncio.sleep(0.03)
    await watchdog.stop()

    assert (await watchdog.get_state()).loop_iteration >= 1


@pytest.mark.asyncio
async def test_queue_failure_sets_last_error_without_crashing(watchdog_module) -> None:
    queue = FakePatrolQueue()
    queue.raise_on_status = True
    watchdog = build_watchdog(watchdog_module, patrol_queue=queue)

    healthy = await watchdog.check_once()

    assert healthy is True
    assert "queue status failed" in (watchdog.last_error() or "")


def test_global_get_patrol_watchdog_returns_watchdog(watchdog_module) -> None:
    assert watchdog_module.get_patrol_watchdog() is watchdog_module.patrol_watchdog
    assert isinstance(watchdog_module.patrol_watchdog, watchdog_module.PatrolWatchdog)
