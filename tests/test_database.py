from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.event_bus import Event, EventName


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    from core.database import Database

    database = Database(tmp_path / "data" / "quadruped.db")
    await database.initialize()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_initialize_creates_tables(tmp_path: Path) -> None:
    from core.database import Database

    database = Database(tmp_path / "runtime" / "quadruped.db")
    try:
        await database.initialize()
        cursor = await database._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('tasks', 'quadruped_telemetry', 'events', 'routes')"
        )
        rows = await cursor.fetchall()

        assert {row[0] for row in rows} == {"tasks", "quadruped_telemetry", "events", "routes"}
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_connect_close_idempotent(tmp_path: Path) -> None:
    from core.database import Database

    database = Database(tmp_path / "runtime" / "quadruped.db")
    try:
        await database.connect()
        first_connection = database._connection
        await database.connect()
        assert database._connection is first_connection
        assert await database.is_connected() is True

        await database.close()
        await database.close()
        assert await database.is_connected() is False
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_memory_database_supported() -> None:
    from core.database import Database

    database = Database(":memory:")
    try:
        await database.initialize()
        task = await database.create_task("A", "QA")

        assert task.station_id == "A"
        assert task.status == "queued"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_create_task_defaults_to_queued(db) -> None:
    task = await db.create_task("A", "QA", priority=1)

    assert task.status == "queued"
    assert task.priority == 1
    assert task.created_at.endswith("+00:00")


@pytest.mark.asyncio
async def test_create_task_rejects_invalid_priority(db) -> None:
    from core.database import DatabaseError

    with pytest.raises(DatabaseError, match="priority"):
        await db.create_task("A", "QA", priority=9)


@pytest.mark.asyncio
async def test_create_task_rejects_empty_station(db) -> None:
    from core.database import DatabaseError

    with pytest.raises(DatabaseError, match="station_id"):
        await db.create_task("", "QA")


@pytest.mark.asyncio
async def test_update_task_status_sets_dispatched_at(db) -> None:
    task = await db.create_task("A", "QA")

    updated = await db.update_task_status(task.id, "dispatched")

    assert updated.status == "dispatched"
    assert updated.dispatched_at is not None
    assert updated.completed_at is None


@pytest.mark.asyncio
async def test_update_task_status_sets_completed_at_for_terminal_status(db) -> None:
    task = await db.create_task("A", "QA")

    updated = await db.update_task_status(task.id, "completed")

    assert updated.status == "completed"
    assert updated.completed_at is not None


@pytest.mark.asyncio
async def test_update_task_status_rejects_invalid_status(db) -> None:
    from core.database import DatabaseError

    task = await db.create_task("A", "QA")

    with pytest.raises(DatabaseError, match="status"):
        await db.update_task_status(task.id, "teleporting")


@pytest.mark.asyncio
async def test_update_missing_task_raises(db) -> None:
    from core.database import DatabaseError

    with pytest.raises(DatabaseError, match="not found"):
        await db.update_task_status("missing-task", "dispatched")


@pytest.mark.asyncio
async def test_get_queued_tasks_orders_priority_desc_created_asc(db, monkeypatch) -> None:
    import core.database as database_module

    timestamps = iter(
        [
            "2026-04-23T00:00:00+00:00",
            "2026-04-23T00:00:01+00:00",
            "2026-04-23T00:00:02+00:00",
        ]
    )
    monkeypatch.setattr(database_module, "utc_now_iso", lambda: next(timestamps))

    first = await db.create_task("A", "QA", priority=1, task_id="task-1")
    second = await db.create_task("B", "QA", priority=2, task_id="task-2")
    third = await db.create_task("C", "QA", priority=2, task_id="task-3")

    queued = await db.get_queued_tasks()

    assert [task.id for task in queued] == [second.id, third.id, first.id]


@pytest.mark.asyncio
async def test_list_tasks_filter_by_status(db) -> None:
    queued = await db.create_task("A", "QA")
    dispatched = await db.create_task("B", "QA")
    await db.update_task_status(dispatched.id, "dispatched")

    tasks = await db.list_tasks(status="dispatched")

    assert [task.id for task in tasks] == [dispatched.id]
    assert queued.id not in [task.id for task in tasks]


@pytest.mark.asyncio
async def test_log_telemetry_inserts_record(db) -> None:
    await db.log_telemetry(battery_pct=88, pos_x=1.2, yaw=0.5, connection_ok=False)

    cursor = await db._connection.execute(
        "SELECT battery_pct, pos_x, yaw, connection_ok FROM quadruped_telemetry ORDER BY id DESC LIMIT 1"
    )
    row = await cursor.fetchone()

    assert row[0] == 88
    assert row[1] == 1.2
    assert row[2] == 0.5
    assert row[3] == 0


@pytest.mark.asyncio
async def test_prune_old_telemetry_deletes_old_rows(db) -> None:
    old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    new_timestamp = datetime.now(timezone.utc).isoformat()

    await db._connection.execute(
        """
        INSERT INTO quadruped_telemetry (timestamp, battery_pct, connection_ok)
        VALUES (?, ?, ?), (?, ?, ?)
        """,
        (old_timestamp, 10, 1, new_timestamp, 90, 1),
    )
    await db._connection.commit()

    deleted = await db.prune_old_telemetry(retention_hours=48)

    assert deleted == 1


@pytest.mark.asyncio
async def test_prune_old_telemetry_rejects_invalid_retention(db) -> None:
    from core.database import DatabaseError

    with pytest.raises(DatabaseError, match="retention_hours"):
        await db.prune_old_telemetry(retention_hours=0)


@pytest.mark.asyncio
async def test_log_event_inserts_and_lists_event(db) -> None:
    event_id = await db.log_event("task.dispatched", {"station_id": "A"}, source="tests", task_id="task-1")

    events = await db.list_events(limit=10)

    assert events[0]["id"] == event_id
    assert events[0]["event_name"] == "task.dispatched"
    assert events[0]["payload"] == {"station_id": "A"}


@pytest.mark.asyncio
async def test_log_bus_event_uses_event_id_and_payload(db) -> None:
    event = Event(
        name=EventName.TASK_COMPLETED,
        payload={"task_id": "task-99"},
        event_id="event-123",
        timestamp=datetime.now(timezone.utc),
        source="tests",
        task_id="task-99",
        correlation_id="corr-1",
    )

    event_id = await db.log_bus_event(event)
    events = await db.list_events(limit=10)

    assert event_id == "event-123"
    assert events[0]["id"] == "event-123"
    assert events[0]["payload"] == {"task_id": "task-99"}


@pytest.mark.asyncio
async def test_list_events_limit_validation(db) -> None:
    from core.database import DatabaseError

    with pytest.raises(DatabaseError, match="limit"):
        await db.list_events(limit=0)

    with pytest.raises(DatabaseError, match="limit"):
        await db.list_events(limit=1001)


@pytest.mark.asyncio
async def test_log_event_rejects_empty_event_name(db) -> None:
    from core.database import DatabaseError

    with pytest.raises(DatabaseError, match="event_name"):
        await db.log_event("")


@pytest.mark.asyncio
async def test_upsert_route_inserts_route(db) -> None:
    route = await db.upsert_route(
        name="station-a-to-qa",
        origin_id="A",
        destination_id="QA",
        waypoints=[{"name": "wp1"}],
    )

    assert route.name == "station-a-to-qa"
    assert route.active is True


@pytest.mark.asyncio
async def test_upsert_route_updates_existing_by_name(db) -> None:
    original = await db.upsert_route(
        name="station-a-to-qa",
        origin_id="A",
        destination_id="QA",
        waypoints=[{"name": "wp1"}],
    )

    updated = await db.upsert_route(
        name="station-a-to-qa",
        origin_id="A",
        destination_id="QA",
        waypoints=[{"name": "wp2"}],
        active=False,
    )

    assert updated.id == original.id
    assert updated.waypoints_json != original.waypoints_json
    assert updated.active is False


@pytest.mark.asyncio
async def test_get_route_returns_active_matching_route(db) -> None:
    await db.upsert_route(
        name="inactive-route",
        origin_id="A",
        destination_id="QA",
        waypoints=[{"name": "old"}],
        active=False,
    )
    active_route = await db.upsert_route(
        name="active-route",
        origin_id="A",
        destination_id="QA",
        waypoints=[{"name": "new"}],
        active=True,
    )

    route = await db.get_route("A", "QA")

    assert route is not None
    assert route.id == active_route.id


@pytest.mark.asyncio
async def test_list_routes_filters_active(db) -> None:
    await db.upsert_route("route-1", "A", "QA", [{"name": "wp1"}], active=True)
    await db.upsert_route("route-2", "B", "QA", [{"name": "wp2"}], active=False)

    active_routes = await db.list_routes(active=True)
    inactive_routes = await db.list_routes(active=False)

    assert [route.name for route in active_routes] == ["route-1"]
    assert [route.name for route in inactive_routes] == ["route-2"]


@pytest.mark.asyncio
async def test_set_route_active_updates_route(db) -> None:
    await db.upsert_route("route-1", "A", "QA", [{"name": "wp1"}], active=True)

    route = await db.set_route_active("route-1", False)

    assert route.active is False


@pytest.mark.asyncio
async def test_set_route_active_missing_raises(db) -> None:
    from core.database import DatabaseError

    with pytest.raises(DatabaseError, match="not found"):
        await db.set_route_active("missing-route", True)


@pytest.mark.asyncio
async def test_upsert_route_rejects_empty_name(db) -> None:
    from core.database import DatabaseError

    with pytest.raises(DatabaseError, match="name"):
        await db.upsert_route("", "A", "QA", [{"name": "wp1"}])
