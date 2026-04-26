from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeCycle:
    def __init__(
        self,
        cycle_id: str = "cycle-1",
        route_id: str = "PATROL_NORTH_LOOP",
        triggered_by: str = "schedule",
        status: str = "scheduled",
    ) -> None:
        self.cycle_id = cycle_id
        self.route_id = route_id
        self.triggered_by = triggered_by
        self.status = status


class FakePatrolQueue:
    def __init__(self) -> None:
        self.status = {"scheduled": 0, "active": 0}
        self.submitted: list[tuple[str, str]] = []
        self.fail_submit = False

    async def submit_cycle(self, route_id: str, triggered_by: str = "manual"):
        if self.fail_submit:
            raise RuntimeError("queue boom")
        self.submitted.append((route_id, triggered_by))
        self.status["scheduled"] = 1
        return FakeCycle(cycle_id=f"cycle-{len(self.submitted)}", route_id=route_id, triggered_by=triggered_by)

    async def get_queue_status(self) -> dict[str, int]:
        return dict(self.status)


@pytest_asyncio.fixture
async def scheduler_env():
    from shared.core.event_bus import EventBus

    module = importlib.import_module("apps.patrol.tasks.patrol_scheduler")
    event_bus = EventBus()
    queue = FakePatrolQueue()
    scheduler = module.PatrolScheduler(
        patrol_queue=queue,
        event_bus=event_bus,
        schedule_enabled=True,
        patrol_interval_seconds=0.05,
        default_route_id="PATROL_NORTH_LOOP",
    )
    yield scheduler, queue, event_bus, module
    await scheduler.stop()
    await event_bus.stop()


def test_invalid_interval_rejected() -> None:
    module = importlib.import_module("apps.patrol.tasks.patrol_scheduler")

    with pytest.raises(module.PatrolSchedulerError, match="patrol_interval_seconds"):
        module.PatrolScheduler(patrol_queue=FakePatrolQueue(), patrol_interval_seconds=0)


def test_empty_default_route_rejected() -> None:
    module = importlib.import_module("apps.patrol.tasks.patrol_scheduler")

    with pytest.raises(module.PatrolSchedulerError, match="default_route_id"):
        module.PatrolScheduler(patrol_queue=FakePatrolQueue(), patrol_interval_seconds=1.0, default_route_id="")


@pytest.mark.asyncio
async def test_run_once_creates_cycle_when_enabled(scheduler_env) -> None:
    scheduler, queue, event_bus, module = scheduler_env
    events = []

    await event_bus.start()

    async def callback(event):
        events.append(event)

    event_bus.subscribe(module.EventName.PATROL_CYCLE_STARTED, callback, subscriber_name="test")

    created = await scheduler.run_once()
    await event_bus.wait_until_idle()
    state = await scheduler.get_state()

    assert created is True
    assert queue.submitted == [("PATROL_NORTH_LOOP", "schedule")]
    assert state.last_cycle_id == "cycle-1"
    assert state.last_result == "scheduled"
    assert len(events) == 1
    assert events[0].payload["status"] == "scheduled"


@pytest.mark.asyncio
async def test_run_once_returns_false_when_schedule_disabled() -> None:
    module = importlib.import_module("apps.patrol.tasks.patrol_scheduler")
    scheduler = module.PatrolScheduler(
        patrol_queue=FakePatrolQueue(),
        event_bus=module.EventBus(),
        schedule_enabled=False,
        patrol_interval_seconds=1.0,
        default_route_id="PATROL_NORTH_LOOP",
    )

    assert await scheduler.run_once() is False


@pytest.mark.asyncio
async def test_run_once_returns_false_when_suspended(scheduler_env) -> None:
    scheduler, queue, _event_bus, _module = scheduler_env

    await scheduler.suspend()

    assert await scheduler.run_once() is False
    assert queue.submitted == []


@pytest.mark.asyncio
async def test_run_once_returns_false_when_charging_inhibited(scheduler_env) -> None:
    scheduler, queue, event_bus, module = scheduler_env

    await scheduler.start()
    await event_bus.publish(module.EventName.BATTERY_CRITICAL, {})
    await event_bus.wait_until_idle()

    assert await scheduler.run_once() is False
    assert queue.submitted == []


@pytest.mark.asyncio
async def test_run_once_returns_false_when_active_cycle_exists(scheduler_env) -> None:
    scheduler, queue, _event_bus, _module = scheduler_env
    queue.status["active"] = 1

    assert await scheduler.run_once() is False
    assert queue.submitted == []


@pytest.mark.asyncio
async def test_run_once_returns_false_when_scheduled_cycle_exists(scheduler_env) -> None:
    scheduler, queue, _event_bus, _module = scheduler_env
    queue.status["scheduled"] = 1

    assert await scheduler.run_once() is False
    assert queue.submitted == []


@pytest.mark.asyncio
async def test_battery_critical_and_recharged_events_toggle_inhibition(scheduler_env) -> None:
    scheduler, _queue, event_bus, module = scheduler_env

    await scheduler.start()
    await event_bus.publish(module.EventName.BATTERY_CRITICAL, {})
    await event_bus.wait_until_idle()
    assert (await scheduler.get_state()).charging_inhibited is True

    await event_bus.publish(module.EventName.BATTERY_RECHARGED, {})
    await event_bus.wait_until_idle()
    assert (await scheduler.get_state()).charging_inhibited is False


@pytest.mark.asyncio
async def test_patrol_suspended_and_resumed_events_toggle_state(scheduler_env) -> None:
    scheduler, _queue, event_bus, module = scheduler_env

    await scheduler.start()
    await event_bus.publish(module.EventName.PATROL_SUSPENDED, {})
    await event_bus.wait_until_idle()
    assert (await scheduler.get_state()).suspended is True

    await event_bus.publish(module.EventName.PATROL_RESUMED, {})
    await event_bus.wait_until_idle()
    assert (await scheduler.get_state()).suspended is False


@pytest.mark.asyncio
async def test_suspend_and_resume_methods_publish_events(scheduler_env) -> None:
    scheduler, _queue, event_bus, module = scheduler_env
    events = []

    await event_bus.start()

    async def callback(event):
        events.append(event.name)

    event_bus.subscribe(module.EventName.PATROL_SUSPENDED, callback, subscriber_name="test-suspend")
    event_bus.subscribe(module.EventName.PATROL_RESUMED, callback, subscriber_name="test-resume")

    await scheduler.suspend("manual")
    await scheduler.resume("manual")
    await event_bus.wait_until_idle()

    assert events == [module.EventName.PATROL_SUSPENDED, module.EventName.PATROL_RESUMED]


@pytest.mark.asyncio
async def test_start_and_stop_are_idempotent(scheduler_env) -> None:
    scheduler, _queue, event_bus, module = scheduler_env

    await scheduler.start()
    await scheduler.start()
    assert scheduler.is_running() is True
    assert event_bus.subscriber_count(module.EventName.BATTERY_CRITICAL) == 1
    assert event_bus.subscriber_count(module.EventName.BATTERY_RECHARGED) == 1

    await scheduler.stop()
    await scheduler.stop()
    assert scheduler.is_running() is False
    assert event_bus.subscriber_count(module.EventName.BATTERY_CRITICAL) == 0
    assert event_bus.subscriber_count(module.EventName.BATTERY_RECHARGED) == 0


@pytest.mark.asyncio
async def test_loop_iteration_increments(scheduler_env) -> None:
    scheduler, queue, _event_bus, _module = scheduler_env

    await scheduler.start()
    await asyncio.sleep(0.08)
    await scheduler.stop()

    state = await scheduler.get_state()

    assert state.loop_iteration >= 1
    assert queue.submitted


@pytest.mark.asyncio
async def test_queue_failure_sets_last_error(scheduler_env) -> None:
    scheduler, queue, _event_bus, _module = scheduler_env
    queue.fail_submit = True

    created = await scheduler.run_once()
    state = await scheduler.get_state()

    assert created is False
    assert "queue boom" in (scheduler.last_error() or "")
    assert "queue boom" in (state.last_result or "")


def test_global_get_patrol_scheduler_returns_scheduler() -> None:
    module = importlib.import_module("apps.patrol.tasks.patrol_scheduler")

    assert module.get_patrol_scheduler() is module.patrol_scheduler
    assert isinstance(module.patrol_scheduler, module.PatrolScheduler)
