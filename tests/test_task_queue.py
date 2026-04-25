from __future__ import annotations

import sys
import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeStationProvider:
    def __init__(self, stations: dict[str, object] | None = None) -> None:
        self.stations = stations or {}

    async def get_station(self, station_id: str):
        station = self.stations.get(station_id)
        if isinstance(station, Exception):
            raise station
        if station is None:
            raise LookupError(f"station not found: {station_id}")
        return station


@pytest_asyncio.fixture
async def task_queue_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core.database import Database
    from core.event_bus import EventBus
    import tasks.queue as queue_module

    event_bus = EventBus()
    await event_bus.start()
    monkeypatch.setattr(queue_module, "get_event_bus", lambda: event_bus)

    database = Database(tmp_path / "queue.db")
    queue = queue_module.TaskQueue(database=database)
    yield queue, database, event_bus, queue_module
    await database.close()
    await event_bus.stop()


@pytest.mark.asyncio
async def test_submit_task_creates_queued_task(task_queue_env) -> None:
    queue, _, _, _ = task_queue_env

    task = await queue.submit_task("A", "QA", priority=1, batch_id="batch-1")

    assert task.status == "queued"
    assert task.station_id == "A"
    assert task.destination_id == "QA"
    assert task.priority == 1


@pytest.mark.asyncio
async def test_submit_task_generates_id_when_missing(task_queue_env) -> None:
    queue, _, _, _ = task_queue_env

    task = await queue.submit_task("A", "QA")

    assert task.id


@pytest.mark.asyncio
async def test_submit_task_rejects_invalid_input(task_queue_env) -> None:
    queue, _, _, queue_module = task_queue_env

    with pytest.raises(queue_module.TaskQueueError):
        await queue.submit_task("", "QA")
    with pytest.raises(queue_module.TaskQueueError):
        await queue.submit_task("A", "")
    with pytest.raises(queue_module.TaskQueueError):
        await queue.submit_task("A", "QA", priority=-1)


@pytest.mark.asyncio
async def test_get_task_returns_task(task_queue_env) -> None:
    queue, _, _, _ = task_queue_env
    submitted = await queue.submit_task("A", "QA", task_id="task-1")

    task = await queue.get_task(submitted.id)

    assert task.id == "task-1"


@pytest.mark.asyncio
async def test_list_tasks_filters_status(task_queue_env) -> None:
    queue, _, _, _ = task_queue_env
    queued = await queue.submit_task("A", "QA")
    dispatched = await queue.submit_task("B", "QA")
    await queue.mark_dispatched(dispatched.id)

    tasks = await queue.list_tasks(status="queued")

    assert [task.id for task in tasks] == [queued.id]


@pytest.mark.asyncio
async def test_get_queue_status_counts_statuses(task_queue_env) -> None:
    queue, _, _, _ = task_queue_env
    queued = await queue.submit_task("A", "QA")
    dispatched = await queue.submit_task("B", "QA")
    cancelled = await queue.submit_task("C", "QA")
    failed = await queue.submit_task("D", "QA")
    completed = await queue.submit_task("E", "QA")

    await queue.mark_dispatched(dispatched.id)
    await queue.cancel_task(cancelled.id)
    await queue.mark_failed(failed.id)
    await queue.mark_dispatched(completed.id)
    await queue.mark_awaiting_load(completed.id)
    await queue.mark_in_transit(completed.id)
    await queue.mark_awaiting_unload(completed.id)
    await queue.mark_completed(completed.id)

    summary = await queue.get_queue_status()

    assert summary.total == 5
    assert summary.queued == 1
    assert summary.dispatched == 1
    assert summary.completed == 1
    assert summary.failed == 1
    assert summary.cancelled == 1


@pytest.mark.asyncio
async def test_get_next_task_returns_none_when_empty(task_queue_env) -> None:
    queue, _, _, _ = task_queue_env

    task = await queue.get_next_task()

    assert task is None


@pytest.mark.asyncio
async def test_get_scored_candidates_sorts_by_score(task_queue_env) -> None:
    queue, _, _, _ = task_queue_env
    low = await queue.submit_task("A", "QA", priority=0)
    high = await queue.submit_task("B", "QA", priority=2)

    scored = await queue.get_scored_candidates()

    assert [candidate.task.id for candidate in scored][:2] == [high.id, low.id]


@pytest.mark.asyncio
async def test_priority_affects_scoring(task_queue_env) -> None:
    _, database, _, queue_module = task_queue_env
    queue = queue_module.TaskQueue(
        database=database,
        priority_weight=100.0,
        recency_weight=0.0,
        proximity_weight=0.0,
        direction_bonus=0.0,
    )
    low = await queue.submit_task("A", "QA", priority=0)
    high = await queue.submit_task("B", "QA", priority=2)

    scored = await queue.get_scored_candidates()

    assert scored[0].task.id == high.id
    assert scored[0].components["priority_component"] > scored[1].components["priority_component"]
    assert scored[1].task.id == low.id


@pytest.mark.asyncio
async def test_recency_affects_scoring(task_queue_env, monkeypatch: pytest.MonkeyPatch) -> None:
    queue, _, _, queue_module = task_queue_env
    import core.database as database_module

    old_time = datetime(2026, 4, 24, 0, 0, 0, tzinfo=timezone.utc)
    new_time = old_time + timedelta(seconds=10)
    timestamps = [old_time.isoformat(), new_time.isoformat()]

    monkeypatch.setattr(database_module, "utc_now_iso", lambda: timestamps.pop(0))

    queue = queue_module.TaskQueue(
        database=queue._database,
        priority_weight=0.0,
        recency_weight=100.0,
        proximity_weight=0.0,
        direction_bonus=0.0,
    )
    old_task = await queue.submit_task("A", "QA")
    new_task = await queue.submit_task("B", "QA")
    monkeypatch.setattr(queue, "_utc_now", lambda: new_time + timedelta(seconds=20))

    scored = await queue.get_scored_candidates()

    assert scored[0].task.id == new_task.id
    assert scored[1].task.id == old_task.id


@pytest.mark.asyncio
async def test_direction_bonus_applies_after_completion(task_queue_env) -> None:
    queue, _, _, queue_module = task_queue_env
    queue = queue_module.TaskQueue(
        database=queue._database,
        priority_weight=0.0,
        recency_weight=0.0,
        proximity_weight=0.0,
        direction_bonus=25.0,
    )
    completed = await queue.submit_task("A", "QA")
    same_destination = await queue.submit_task("B", "QA")
    other_destination = await queue.submit_task("C", "DOCK")

    await queue.mark_dispatched(completed.id)
    await queue.mark_awaiting_load(completed.id)
    await queue.mark_in_transit(completed.id)
    await queue.mark_awaiting_unload(completed.id)
    await queue.mark_completed(completed.id)

    scored = await queue.get_scored_candidates()

    assert scored[0].task.id == same_destination.id
    assert scored[0].components["direction_bonus_component"] == 25.0
    assert scored[1].task.id == other_destination.id


@pytest.mark.asyncio
async def test_proximity_component_zero_without_station_provider(task_queue_env) -> None:
    _, database, _, queue_module = task_queue_env
    queue = queue_module.TaskQueue(
        database=database,
        priority_weight=0.0,
        recency_weight=0.0,
        proximity_weight=10.0,
        direction_bonus=0.0,
    )
    await queue.submit_task("A", "QA")

    scored = await queue.get_scored_candidates(robot_position=(0.0, 0.0))

    assert scored[0].components["proximity_component"] == 0.0
    assert "distance_to_station" not in scored[0].components


@pytest.mark.asyncio
async def test_proximity_component_zero_without_robot_position(task_queue_env) -> None:
    _, database, _, queue_module = task_queue_env
    queue = queue_module.TaskQueue(
        database=database,
        station_provider=FakeStationProvider(
            {"A": SimpleNamespace(id="A", x=1.0, y=1.0)}
        ),
        priority_weight=0.0,
        recency_weight=0.0,
        proximity_weight=10.0,
        direction_bonus=0.0,
    )
    await queue.submit_task("A", "QA")

    scored = await queue.get_scored_candidates()

    assert scored[0].components["proximity_component"] == 0.0
    assert "distance_to_station" not in scored[0].components


@pytest.mark.asyncio
async def test_closer_station_scores_higher_when_station_provider_available(task_queue_env) -> None:
    _, database, _, queue_module = task_queue_env
    queue = queue_module.TaskQueue(
        database=database,
        station_provider=FakeStationProvider(
            {
                "A": SimpleNamespace(id="A", x=1.0, y=0.0),
                "B": SimpleNamespace(id="B", x=10.0, y=0.0),
            }
        ),
        priority_weight=0.0,
        recency_weight=0.0,
        proximity_weight=100.0,
        direction_bonus=0.0,
    )
    near_task = await queue.submit_task("A", "QA")
    far_task = await queue.submit_task("B", "QA")

    scored = await queue.get_scored_candidates(robot_position=(0.0, 0.0))

    assert [candidate.task.id for candidate in scored][:2] == [near_task.id, far_task.id]
    assert scored[0].components["proximity_component"] > scored[1].components["proximity_component"]


@pytest.mark.asyncio
async def test_missing_station_does_not_crash_scoring(task_queue_env) -> None:
    _, database, _, queue_module = task_queue_env
    queue = queue_module.TaskQueue(
        database=database,
        station_provider=FakeStationProvider(),
        priority_weight=0.0,
        recency_weight=0.0,
        proximity_weight=10.0,
        direction_bonus=0.0,
    )
    task = await queue.submit_task("A", "QA")

    scored = await queue.get_scored_candidates(robot_position=(0.0, 0.0))

    assert scored[0].task.id == task.id
    assert scored[0].components["proximity_component"] == 0.0


@pytest.mark.asyncio
async def test_station_without_coordinates_does_not_crash_scoring(task_queue_env) -> None:
    _, database, _, queue_module = task_queue_env
    queue = queue_module.TaskQueue(
        database=database,
        station_provider=FakeStationProvider(
            {"A": SimpleNamespace(id="A", x=None, y=None)}
        ),
        priority_weight=0.0,
        recency_weight=0.0,
        proximity_weight=10.0,
        direction_bonus=0.0,
    )
    task = await queue.submit_task("A", "QA")

    scored = await queue.get_scored_candidates(robot_position=(0.0, 0.0))

    assert scored[0].task.id == task.id
    assert scored[0].components["proximity_component"] == 0.0
    assert "distance_to_station" not in scored[0].components


@pytest.mark.asyncio
async def test_components_include_distance_when_available(task_queue_env) -> None:
    _, database, _, queue_module = task_queue_env
    queue = queue_module.TaskQueue(
        database=database,
        station_provider=FakeStationProvider(
            {"A": SimpleNamespace(id="A", x=3.0, y=4.0)}
        ),
        priority_weight=0.0,
        recency_weight=0.0,
        proximity_weight=1.0,
        direction_bonus=0.0,
    )
    await queue.submit_task("A", "QA")

    scored = await queue.get_scored_candidates(robot_position=(0.0, 0.0))

    assert scored[0].components["distance_to_station"] == 5.0
    assert scored[0].components["proximity_component"] == 0.2


@pytest.mark.asyncio
async def test_mark_dispatched_valid_transition(task_queue_env) -> None:
    queue, _, _, _ = task_queue_env
    task = await queue.submit_task("A", "QA")

    updated = await queue.mark_dispatched(task.id)

    assert updated.status == "dispatched"
    assert updated.dispatched_at is not None


@pytest.mark.asyncio
async def test_mark_awaiting_load_valid_transition(task_queue_env) -> None:
    queue, _, _, _ = task_queue_env
    task = await queue.submit_task("A", "QA")
    await queue.mark_dispatched(task.id)

    updated = await queue.mark_awaiting_load(task.id)

    assert updated.status == "awaiting_load"


@pytest.mark.asyncio
async def test_mark_in_transit_valid_transition(task_queue_env) -> None:
    queue, _, _, _ = task_queue_env
    task = await queue.submit_task("A", "QA")
    await queue.mark_dispatched(task.id)
    await queue.mark_awaiting_load(task.id)

    updated = await queue.mark_in_transit(task.id)

    assert updated.status == "in_transit"


@pytest.mark.asyncio
async def test_mark_awaiting_unload_valid_transition(task_queue_env) -> None:
    queue, _, _, _ = task_queue_env
    task = await queue.submit_task("A", "QA")
    await queue.mark_dispatched(task.id)
    await queue.mark_awaiting_load(task.id)
    await queue.mark_in_transit(task.id)

    updated = await queue.mark_awaiting_unload(task.id)

    assert updated.status == "awaiting_unload"


@pytest.mark.asyncio
async def test_mark_completed_valid_transition_sets_completed_at(task_queue_env) -> None:
    queue, _, _, _ = task_queue_env
    task = await queue.submit_task("A", "QA")
    await queue.mark_dispatched(task.id)
    await queue.mark_awaiting_load(task.id)
    await queue.mark_in_transit(task.id)
    await queue.mark_awaiting_unload(task.id)

    updated = await queue.mark_completed(task.id)

    assert updated.status == "completed"
    assert updated.completed_at is not None


@pytest.mark.asyncio
async def test_mark_failed_valid_transition(task_queue_env) -> None:
    queue, _, _, _ = task_queue_env
    task = await queue.submit_task("A", "QA")

    updated = await queue.mark_failed(task.id)

    assert updated.status == "failed"


@pytest.mark.asyncio
async def test_cancel_task_valid_transition(task_queue_env) -> None:
    queue, _, _, _ = task_queue_env
    task = await queue.submit_task("A", "QA")

    updated = await queue.cancel_task(task.id)

    assert updated.status == "cancelled"


@pytest.mark.asyncio
async def test_invalid_transition_raises(task_queue_env) -> None:
    queue, _, _, queue_module = task_queue_env
    task = await queue.submit_task("A", "QA")

    with pytest.raises(queue_module.InvalidTaskTransitionError):
        await queue.mark_completed(task.id)


@pytest.mark.asyncio
async def test_terminal_tasks_cannot_transition(task_queue_env) -> None:
    queue, _, _, queue_module = task_queue_env
    task = await queue.submit_task("A", "QA")
    await queue.mark_failed(task.id)

    with pytest.raises(queue_module.InvalidTaskTransitionError):
        await queue.mark_dispatched(task.id)


@pytest.mark.asyncio
async def test_task_status_events_are_published(task_queue_env) -> None:
    queue, _, event_bus, _ = task_queue_env
    events = []
    event_bus.subscribe("*", lambda event: events.append(event))

    task = await queue.submit_task("A", "QA", notes="queued")
    await queue.mark_failed(task.id, notes="jammed")
    await event_bus.wait_until_idle(timeout=1.0)

    names = [event.name.value for event in events]

    assert "task.submitted" in names
    assert "task.status_changed" in names
    assert "task.failed" in names


def test_global_get_task_queue_returns_queue() -> None:
    from tasks.queue import TaskQueue, get_task_queue

    assert isinstance(get_task_queue(), TaskQueue)


def test_global_get_task_queue_uses_route_store_station_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_station_provider = object()

    import shared.navigation.route_store as route_store_module

    monkeypatch.setattr(route_store_module, "get_route_store", lambda: fake_station_provider)
    sys.modules.pop("apps.logistics.tasks.queue", None)
    sys.modules.pop("tasks.queue", None)
    queue_module = importlib.import_module("apps.logistics.tasks.queue")

    assert queue_module.get_task_queue()._station_provider is fake_station_provider
