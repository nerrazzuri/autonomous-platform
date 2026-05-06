from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.patrol import events as patrol_events


@pytest_asyncio.fixture
async def queue_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from shared.core.database import Database
    from shared.core.event_bus import EventBus

    from apps.patrol.tasks.patrol_queue import PatrolQueue

    database = Database(tmp_path / "data" / "quadruped.db")
    event_bus = EventBus()

    import apps.patrol.tasks.patrol_queue as queue_module

    monkeypatch.setattr(queue_module, "get_event_bus", lambda: event_bus)

    queue = PatrolQueue(database=database)
    yield queue, database, event_bus, queue_module
    await event_bus.stop()
    await database.close()


@pytest.mark.asyncio
async def test_initialize_creates_table(queue_env) -> None:
    queue, database, _event_bus, _queue_module = queue_env

    await queue.initialize()

    cursor = await database._connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='patrol_cycles'"
    )
    row = await cursor.fetchone()

    assert row[0] == "patrol_cycles"


@pytest.mark.asyncio
async def test_submit_cycle_returns_scheduled_record(queue_env) -> None:
    queue, _database, _event_bus, queue_module = queue_env

    record = await queue.submit_cycle("PATROL_NORTH_LOOP", triggered_by="manual", cycle_id="cycle-1", waypoints_total=4)

    assert record.cycle_id == "cycle-1"
    assert record.route_id == "PATROL_NORTH_LOOP"
    assert record.status is queue_module.PatrolCycleStatus.SCHEDULED
    assert record.triggered_by == "manual"
    assert record.waypoints_total == 4


@pytest.mark.asyncio
async def test_submit_cycle_generates_uuid(queue_env) -> None:
    from uuid import UUID

    queue, _database, _event_bus, _queue_module = queue_env

    record = await queue.submit_cycle("PATROL_NORTH_LOOP")

    assert UUID(record.cycle_id)


@pytest.mark.asyncio
async def test_get_cycle_returns_record(queue_env) -> None:
    queue, _database, _event_bus, _queue_module = queue_env

    created = await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="cycle-1")
    fetched = await queue.get_cycle("cycle-1")

    assert fetched == created


@pytest.mark.asyncio
async def test_get_cycle_missing_raises(queue_env) -> None:
    queue, _database, _event_bus, queue_module = queue_env

    with pytest.raises(queue_module.PatrolCycleNotFound):
        await queue.get_cycle("missing")


@pytest.mark.asyncio
async def test_get_next_cycle_returns_oldest_scheduled(queue_env, monkeypatch: pytest.MonkeyPatch) -> None:
    queue, _database, _event_bus, queue_module = queue_env

    timestamps = iter(
        [
            "2026-04-26T00:00:00+00:00",
            "2026-04-26T00:00:01+00:00",
        ]
    )
    monkeypatch.setattr(queue_module, "utc_now_iso", lambda: next(timestamps))

    first = await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="cycle-1")
    second = await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="cycle-2")

    next_cycle = await queue.get_next_cycle()

    assert next_cycle == first
    assert next_cycle.cycle_id != second.cycle_id


@pytest.mark.asyncio
async def test_mark_active_valid_transition(queue_env) -> None:
    queue, _database, _event_bus, queue_module = queue_env

    created = await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="cycle-1")
    updated = await queue.mark_active(created.cycle_id)

    assert updated.status is queue_module.PatrolCycleStatus.ACTIVE
    assert updated.started_at is not None


@pytest.mark.asyncio
async def test_mark_completed_updates_stats(queue_env) -> None:
    queue, _database, _event_bus, queue_module = queue_env

    created = await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="cycle-1", waypoints_total=2)
    await queue.mark_active(created.cycle_id)
    updated = await queue.mark_completed(
        created.cycle_id,
        stats_dict={"waypoints_total": 2, "waypoints_observed": 2, "anomaly_ids": ["anom-1"]},
    )

    assert updated.status is queue_module.PatrolCycleStatus.COMPLETED
    assert updated.completed_at is not None
    assert updated.waypoints_total == 2
    assert updated.waypoints_observed == 2
    assert updated.anomaly_ids == ["anom-1"]


@pytest.mark.asyncio
async def test_mark_failed_sets_reason(queue_env) -> None:
    queue, _database, _event_bus, queue_module = queue_env

    created = await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="cycle-1")
    await queue.mark_active(created.cycle_id)
    updated = await queue.mark_failed(created.cycle_id, "camera timeout")

    assert updated.status is queue_module.PatrolCycleStatus.FAILED
    assert updated.completed_at is not None
    assert updated.failure_reason == "camera timeout"


@pytest.mark.asyncio
async def test_suspend_and_resume_cycle(queue_env) -> None:
    queue, _database, _event_bus, queue_module = queue_env

    created = await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="cycle-1")
    await queue.mark_active(created.cycle_id)
    suspended = await queue.suspend_cycle(created.cycle_id, reason="manual hold")
    resumed = await queue.resume_cycle(created.cycle_id)

    assert suspended.status is queue_module.PatrolCycleStatus.SUSPENDED
    assert suspended.failure_reason == "manual hold"
    assert resumed.status is queue_module.PatrolCycleStatus.ACTIVE


@pytest.mark.asyncio
async def test_invalid_transition_raises(queue_env) -> None:
    queue, _database, _event_bus, queue_module = queue_env

    created = await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="cycle-1")

    with pytest.raises(queue_module.InvalidCycleTransition):
        await queue.mark_completed(created.cycle_id)


@pytest.mark.asyncio
async def test_queue_status_counts_all_statuses(queue_env) -> None:
    queue, _database, _event_bus, _queue_module = queue_env

    scheduled = await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="scheduled")
    active = await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="active")
    completed = await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="completed")
    failed = await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="failed")
    suspended = await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="suspended")

    await queue.mark_active(active.cycle_id)
    await queue.mark_active(completed.cycle_id)
    await queue.mark_completed(completed.cycle_id)
    await queue.mark_active(failed.cycle_id)
    await queue.mark_failed(failed.cycle_id, "boom")
    await queue.mark_active(suspended.cycle_id)
    await queue.suspend_cycle(suspended.cycle_id)

    status = await queue.get_queue_status()

    assert status == {
        "scheduled": 1,
        "active": 1,
        "completed": 1,
        "failed": 1,
        "suspended": 1,
        "total": 5,
    }
    assert scheduled.cycle_id == "scheduled"


@pytest.mark.asyncio
async def test_history_ordered_desc(queue_env, monkeypatch: pytest.MonkeyPatch) -> None:
    queue, _database, _event_bus, queue_module = queue_env

    timestamps = iter(
        [
            "2026-04-26T00:00:00+00:00",
            "2026-04-26T00:00:01+00:00",
            "2026-04-26T00:00:02+00:00",
        ]
    )
    monkeypatch.setattr(queue_module, "utc_now_iso", lambda: next(timestamps))

    await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="cycle-1")
    await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="cycle-2")
    await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="cycle-3")

    history = await queue.get_cycle_history()

    assert [record.cycle_id for record in history[:3]] == ["cycle-3", "cycle-2", "cycle-1"]


@pytest.mark.asyncio
async def test_events_published_on_transitions(queue_env) -> None:
    from shared.core.event_bus import EventName

    queue, _database, event_bus, _queue_module = queue_env
    received = []

    async def callback(event):
        received.append(event.name)

    await event_bus.start()
    event_bus.subscribe("*", callback)

    created = await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="cycle-1")
    await queue.mark_active(created.cycle_id)
    await queue.suspend_cycle(created.cycle_id)
    await queue.resume_cycle(created.cycle_id)
    await queue.mark_completed(created.cycle_id, stats_dict={"waypoints_total": 1, "waypoints_observed": 1})
    await event_bus.wait_until_idle(timeout=0.5)

    created_failed = await queue.submit_cycle("PATROL_NORTH_LOOP", cycle_id="cycle-2")
    await queue.mark_active(created_failed.cycle_id)
    await queue.mark_failed(created_failed.cycle_id, "sensor lost")
    await event_bus.wait_until_idle(timeout=0.5)

    assert received == [
        patrol_events.PATROL_CYCLE_STARTED,
        patrol_events.PATROL_SUSPENDED,
        patrol_events.PATROL_RESUMED,
        patrol_events.PATROL_CYCLE_COMPLETED,
        patrol_events.PATROL_CYCLE_STARTED,
        patrol_events.PATROL_CYCLE_FAILED,
    ]
