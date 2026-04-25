from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_state(*, connection_ok: bool = True):
    from quadruped.sdk_adapter import QuadrupedMode
    from quadruped.state_monitor import QuadrupedState

    return QuadrupedState(
        timestamp=datetime.now(timezone.utc),
        battery_pct=100,
        position=(1.0, 2.0, 0.0),
        rpy=(0.0, 0.0, 0.0),
        control_mode=0,
        connection_ok=connection_ok,
        mode=QuadrupedMode.STANDING,
    )


def make_task_record(
    task_id: str,
    *,
    station_id: str = "A",
    destination_id: str = "QA",
    priority: int = 0,
    status: str = "queued",
    notes: str | None = None,
):
    from core.database import TaskRecord

    return TaskRecord(
        id=task_id,
        station_id=station_id,
        destination_id=destination_id,
        batch_id=None,
        priority=priority,
        status=status,
        created_at=datetime.now(timezone.utc).isoformat(),
        dispatched_at=None,
        completed_at=None,
        notes=notes,
    )


class FakeTaskQueue:
    def __init__(self):
        self.tasks: dict[str, object] = {}
        self.robot_positions: list[tuple[float, float] | None] = []
        self.actions: list[tuple[str, str, str | None]] = []

    def add_task(self, task):
        self.tasks[task.id] = task
        return task

    async def get_next_task(self, robot_position=None):
        self.robot_positions.append(robot_position)
        for task in self.tasks.values():
            if task.status == "queued":
                return task
        return None

    async def get_task(self, task_id: str):
        return self.tasks[task_id]

    async def list_tasks(self, status: str | None = None, limit: int = 100, offset: int = 0):
        tasks = list(self.tasks.values())
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        return tasks[offset : offset + limit]

    async def mark_dispatched(self, task_id: str, notes: str | None = None):
        return self._update(task_id, "dispatched", notes, set_dispatched=True)

    async def mark_awaiting_load(self, task_id: str, notes: str | None = None):
        return self._update(task_id, "awaiting_load", notes)

    async def mark_in_transit(self, task_id: str, notes: str | None = None):
        return self._update(task_id, "in_transit", notes)

    async def mark_awaiting_unload(self, task_id: str, notes: str | None = None):
        return self._update(task_id, "awaiting_unload", notes)

    async def mark_completed(self, task_id: str, notes: str | None = None):
        return self._update(task_id, "completed", notes, set_completed=True)

    async def mark_failed(self, task_id: str, notes: str | None = None):
        return self._update(task_id, "failed", notes)

    async def cancel_task(self, task_id: str, notes: str | None = None):
        return self._update(task_id, "cancelled", notes)

    def _update(self, task_id: str, status: str, notes: str | None, *, set_dispatched=False, set_completed=False):
        task = self.tasks[task_id]
        now = datetime.now(timezone.utc).isoformat()
        updated = replace(
            task,
            status=status,
            notes=notes if notes is not None else task.notes,
            dispatched_at=now if set_dispatched and task.dispatched_at is None else task.dispatched_at,
            completed_at=now if set_completed and task.completed_at is None else task.completed_at,
        )
        self.tasks[task_id] = updated
        self.actions.append((status, task_id, notes))
        return updated


class FakeNavigator:
    def __init__(self):
        from navigation.navigator import NavigationResult

        self.busy = False
        self.calls: list[tuple[str, str, str | None]] = []
        self.release_event: asyncio.Event | None = None
        self.started_event = asyncio.Event()
        self.on_execute = None
        self.result = NavigationResult(
            success=True,
            route_id="A_TO_QA",
            origin_id="A",
            destination_id="QA",
            completed_waypoints=2,
            total_waypoints=2,
        )

    def is_navigating(self):
        return self.busy

    async def execute_route(self, origin_id, destination_id, task_id=None):
        self.busy = True
        self.calls.append((origin_id, destination_id, task_id))
        self.started_event.set()
        try:
            if self.on_execute is not None:
                await self.on_execute(origin_id, destination_id, task_id)
            if self.release_event is not None:
                await self.release_event.wait()
            return self.result
        finally:
            self.busy = False


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


class FakeStationProvider:
    def __init__(self, stations: dict[str, object]) -> None:
        self.stations = stations

    async def get_station(self, station_id: str):
        station = self.stations.get(station_id)
        if station is None:
            raise LookupError(f"station not found: {station_id}")
        return station


@pytest_asyncio.fixture
async def dispatcher_env(monkeypatch: pytest.MonkeyPatch):
    from core.event_bus import EventBus
    import tasks.dispatcher as dispatcher_module

    event_bus = EventBus()
    await event_bus.start()
    monkeypatch.setattr(dispatcher_module, "get_event_bus", lambda: event_bus)
    yield dispatcher_module, event_bus
    await event_bus.stop()


@pytest.mark.asyncio
async def test_dispatch_once_returns_false_when_no_tasks(dispatcher_env) -> None:
    dispatcher_module, _ = dispatcher_env
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=FakeTaskQueue(),
        navigator=FakeNavigator(),
        state_monitor=FakeStateMonitor(state=make_state()),
    )

    processed = await dispatcher.dispatch_once()

    assert processed is False


@pytest.mark.asyncio
async def test_dispatch_once_returns_false_when_paused(dispatcher_env) -> None:
    dispatcher_module, _ = dispatcher_env
    queue = FakeTaskQueue()
    queue.add_task(make_task_record("task-1"))
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=queue,
        navigator=FakeNavigator(),
        state_monitor=FakeStateMonitor(state=make_state()),
    )
    await dispatcher.pause()

    processed = await dispatcher.dispatch_once()

    assert processed is False


@pytest.mark.asyncio
async def test_dispatch_once_returns_false_when_navigator_busy(dispatcher_env) -> None:
    dispatcher_module, _ = dispatcher_env
    queue = FakeTaskQueue()
    queue.add_task(make_task_record("task-1"))
    navigator = FakeNavigator()
    navigator.busy = True
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=queue,
        navigator=navigator,
        state_monitor=FakeStateMonitor(state=make_state()),
    )

    processed = await dispatcher.dispatch_once()

    assert processed is False


@pytest.mark.asyncio
async def test_dispatch_once_returns_false_when_no_state(dispatcher_env) -> None:
    dispatcher_module, _ = dispatcher_env
    queue = FakeTaskQueue()
    queue.add_task(make_task_record("task-1"))
    state_monitor = FakeStateMonitor(state=None, poll_state=None)
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=queue,
        navigator=FakeNavigator(),
        state_monitor=state_monitor,
    )

    processed = await dispatcher.dispatch_once()

    assert processed is False
    assert state_monitor.poll_called is True


@pytest.mark.asyncio
async def test_dispatch_once_returns_false_when_disconnected(dispatcher_env) -> None:
    dispatcher_module, _ = dispatcher_env
    queue = FakeTaskQueue()
    queue.add_task(make_task_record("task-1"))
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=queue,
        navigator=FakeNavigator(),
        state_monitor=FakeStateMonitor(state=make_state(connection_ok=False)),
    )

    processed = await dispatcher.dispatch_once()

    assert processed is False


@pytest.mark.asyncio
async def test_dispatch_once_dispatches_task_and_calls_navigator(dispatcher_env) -> None:
    dispatcher_module, _ = dispatcher_env
    queue = FakeTaskQueue()
    task = queue.add_task(make_task_record("task-1", station_id="A", destination_id="QA"))
    navigator = FakeNavigator()
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=queue,
        navigator=navigator,
        state_monitor=FakeStateMonitor(state=make_state()),
    )

    processed = await dispatcher.dispatch_once()

    assert processed is True
    assert navigator.calls == [("A", "QA", task.id)]
    assert queue.tasks[task.id].status == "completed"
    assert queue.robot_positions[-1] == (1.0, 2.0)


@pytest.mark.asyncio
async def test_dispatch_once_uses_station_provider_backed_queue_scoring(
    dispatcher_env,
    tmp_path: Path,
) -> None:
    dispatcher_module, _ = dispatcher_env
    from core.database import Database
    import tasks.queue as queue_module

    database = Database(tmp_path / "dispatcher-queue.db")
    queue = queue_module.TaskQueue(
        database=database,
        station_provider=FakeStationProvider(
            {
                "A": SimpleNamespace(id="A", x=1.0, y=2.0),
                "B": SimpleNamespace(id="B", x=50.0, y=50.0),
            }
        ),
        priority_weight=0.0,
        recency_weight=0.0,
        proximity_weight=100.0,
        direction_bonus=0.0,
    )
    near_task = await queue.submit_task("A", "QA")
    await queue.submit_task("B", "QA")
    navigator = FakeNavigator()
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=queue,
        navigator=navigator,
        state_monitor=FakeStateMonitor(state=make_state()),
    )

    try:
        processed = await dispatcher.dispatch_once()
    finally:
        await database.close()

    assert processed is True
    assert navigator.calls == [("A", "QA", near_task.id)]


@pytest.mark.asyncio
async def test_start_and_stop_are_idempotent(dispatcher_env) -> None:
    dispatcher_module, _ = dispatcher_env
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=FakeTaskQueue(),
        navigator=FakeNavigator(),
        state_monitor=FakeStateMonitor(state=make_state()),
        poll_interval_seconds=0.01,
    )

    await dispatcher.start()
    await dispatcher.start()
    assert dispatcher.is_running() is True

    await dispatcher.stop()
    await dispatcher.stop()
    assert dispatcher.is_running() is False


@pytest.mark.asyncio
async def test_pause_and_resume_are_idempotent(dispatcher_env) -> None:
    dispatcher_module, _ = dispatcher_env
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=FakeTaskQueue(),
        navigator=FakeNavigator(),
        state_monitor=FakeStateMonitor(state=make_state()),
    )

    await dispatcher.pause()
    await dispatcher.pause()
    assert dispatcher.is_paused() is True

    await dispatcher.resume()
    await dispatcher.resume()
    assert dispatcher.is_paused() is False


@pytest.mark.asyncio
async def test_active_task_state_updates_during_dispatch(dispatcher_env) -> None:
    dispatcher_module, _ = dispatcher_env
    queue = FakeTaskQueue()
    task = queue.add_task(make_task_record("task-1", station_id="A", destination_id="QA"))
    navigator = FakeNavigator()
    navigator.release_event = asyncio.Event()
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=queue,
        navigator=navigator,
        state_monitor=FakeStateMonitor(state=make_state()),
    )

    dispatch_task = asyncio.create_task(dispatcher.dispatch_once())
    await navigator.started_event.wait()
    state = await dispatcher.get_state()

    assert state.active_task_id == task.id
    assert state.active_route_origin == "A"
    assert state.active_route_destination == "QA"

    navigator.release_event.set()
    await dispatch_task


@pytest.mark.asyncio
async def test_navigation_blocked_marks_task_failed(dispatcher_env) -> None:
    dispatcher_module, _ = dispatcher_env
    queue = FakeTaskQueue()
    task = queue.add_task(make_task_record("task-1"))
    navigator = FakeNavigator()
    navigator.result = replace(navigator.result, success=False, blocked=True, message="Obstacle timeout")
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=queue,
        navigator=navigator,
        state_monitor=FakeStateMonitor(state=make_state()),
    )

    await dispatcher.dispatch_once()

    assert queue.tasks[task.id].status == "failed"
    assert queue.tasks[task.id].notes == "navigation blocked"


@pytest.mark.asyncio
async def test_navigation_cancelled_marks_task_failed(dispatcher_env) -> None:
    dispatcher_module, _ = dispatcher_env
    queue = FakeTaskQueue()
    task = queue.add_task(make_task_record("task-1"))
    navigator = FakeNavigator()
    navigator.result = replace(navigator.result, success=False, cancelled=True, message="cancelled")
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=queue,
        navigator=navigator,
        state_monitor=FakeStateMonitor(state=make_state()),
    )

    await dispatcher.dispatch_once()

    assert queue.tasks[task.id].status == "failed"
    assert queue.tasks[task.id].notes == "navigation cancelled"


@pytest.mark.asyncio
async def test_navigation_failure_marks_task_failed(dispatcher_env) -> None:
    dispatcher_module, _ = dispatcher_env
    queue = FakeTaskQueue()
    task = queue.add_task(make_task_record("task-1"))
    navigator = FakeNavigator()
    navigator.result = replace(navigator.result, success=False, message="route failure")
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=queue,
        navigator=navigator,
        state_monitor=FakeStateMonitor(state=make_state()),
    )

    await dispatcher.dispatch_once()

    assert queue.tasks[task.id].status == "failed"
    assert queue.tasks[task.id].notes == "route failure"


@pytest.mark.asyncio
async def test_hold_arrival_marks_awaiting_load_then_awaiting_unload(dispatcher_env) -> None:
    dispatcher_module, event_bus = dispatcher_env
    queue = FakeTaskQueue()
    task = queue.add_task(make_task_record("task-1"))
    navigator = FakeNavigator()
    navigator.release_event = asyncio.Event()
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=queue,
        navigator=navigator,
        state_monitor=FakeStateMonitor(state=make_state()),
    )

    dispatch_task = asyncio.create_task(dispatcher.dispatch_once())
    await navigator.started_event.wait()
    await event_bus.publish(
        "quadruped.arrived_at_waypoint",
        {"task_id": task.id, "hold": True},
        task_id=task.id,
    )
    await event_bus.wait_until_idle(timeout=1.0)
    assert queue.tasks[task.id].status == "awaiting_load"

    await event_bus.publish(
        "quadruped.arrived_at_waypoint",
        {"task_id": task.id, "hold": True},
        task_id=task.id,
    )
    await event_bus.wait_until_idle(timeout=1.0)
    assert queue.tasks[task.id].status == "awaiting_unload"

    navigator.release_event.set()
    await dispatch_task


@pytest.mark.asyncio
async def test_human_confirm_load_marks_in_transit(dispatcher_env) -> None:
    dispatcher_module, event_bus = dispatcher_env
    queue = FakeTaskQueue()
    task = queue.add_task(make_task_record("task-1"))
    navigator = FakeNavigator()
    navigator.release_event = asyncio.Event()
    navigator.result = replace(navigator.result, success=False, cancelled=True, message="cancelled")
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=queue,
        navigator=navigator,
        state_monitor=FakeStateMonitor(state=make_state()),
    )

    dispatch_task = asyncio.create_task(dispatcher.dispatch_once())
    await navigator.started_event.wait()
    await event_bus.publish(
        "quadruped.arrived_at_waypoint",
        {"task_id": task.id, "hold": True},
        task_id=task.id,
    )
    await event_bus.wait_until_idle(timeout=1.0)
    await event_bus.publish("human.confirmed_load", {"task_id": task.id}, task_id=task.id)
    await event_bus.wait_until_idle(timeout=1.0)

    assert queue.tasks[task.id].status == "in_transit"

    navigator.release_event.set()
    await dispatch_task


@pytest.mark.asyncio
async def test_human_confirm_unload_marks_completed(dispatcher_env) -> None:
    dispatcher_module, event_bus = dispatcher_env
    queue = FakeTaskQueue()
    task = queue.add_task(make_task_record("task-1"))
    navigator = FakeNavigator()
    navigator.release_event = asyncio.Event()
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=queue,
        navigator=navigator,
        state_monitor=FakeStateMonitor(state=make_state()),
    )

    dispatch_task = asyncio.create_task(dispatcher.dispatch_once())
    await navigator.started_event.wait()
    await event_bus.publish(
        "quadruped.arrived_at_waypoint",
        {"task_id": task.id, "hold": True},
        task_id=task.id,
    )
    await event_bus.wait_until_idle(timeout=1.0)
    await event_bus.publish("human.confirmed_load", {"task_id": task.id}, task_id=task.id)
    await event_bus.wait_until_idle(timeout=1.0)
    await event_bus.publish(
        "quadruped.arrived_at_waypoint",
        {"task_id": task.id, "hold": True},
        task_id=task.id,
    )
    await event_bus.wait_until_idle(timeout=1.0)
    await event_bus.publish("human.confirmed_unload", {"task_id": task.id}, task_id=task.id)
    await event_bus.wait_until_idle(timeout=1.0)

    assert queue.tasks[task.id].status == "completed"

    navigator.release_event.set()
    await dispatch_task


@pytest.mark.asyncio
async def test_dispatcher_clears_active_state_after_finish(dispatcher_env) -> None:
    dispatcher_module, _ = dispatcher_env
    queue = FakeTaskQueue()
    task = queue.add_task(make_task_record("task-1"))
    dispatcher = dispatcher_module.Dispatcher(
        task_queue=queue,
        navigator=FakeNavigator(),
        state_monitor=FakeStateMonitor(state=make_state()),
    )

    await dispatcher.dispatch_once()
    state = await dispatcher.get_state()

    assert state.active_task_id is None
    assert state.active_route_origin is None
    assert state.active_route_destination is None
    assert queue.tasks[task.id].status == "completed"


def test_global_get_dispatcher_returns_dispatcher() -> None:
    from tasks.dispatcher import Dispatcher, get_dispatcher

    assert isinstance(get_dispatcher(), Dispatcher)
