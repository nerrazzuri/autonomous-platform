import pytest

from autonomous_logistic.core.errors import InvalidTaskTransition
from autonomous_logistic.core.models import AuditEventType, Station, TaskStatus, TransportTask
from autonomous_logistic.logging.audit import AuditLogger
from autonomous_logistic.state.repositories import RepositoryRegistry


def test_sqlite_repositories_persist_tasks_stations_and_audit_events(tmp_path):
    db_path = tmp_path / "robot.db"
    registry = RepositoryRegistry(str(db_path))
    registry.initialize()

    station = Station(
        station_id="STATION_A",
        name="Station A",
        position={"type": "generic", "label": "A"},
        metadata={"zone": "factory-floor"},
    )
    task = TransportTask.create(
        source_point="STATION_A",
        destination_point="STATION_B",
        requested_by="operator-1",
        request_source="remote_dispatch",
        notes="first run",
    )

    registry.stations.upsert(station)
    registry.tasks.create(task)
    registry.tasks.update_status(task.task_id, TaskStatus.CANCELLED, error_code="USER_CANCELLED")

    logger = AuditLogger(registry.events)
    logger.record(
        event_type=AuditEventType.TASK_CANCELLED,
        task_id=task.task_id,
        metadata={"reason": "operator request"},
    )

    loaded_task = registry.tasks.get(task.task_id)
    loaded_station = registry.stations.list_all()[0]
    events = registry.events.list_for_task(task.task_id)

    assert loaded_task is not None
    assert loaded_task.status is TaskStatus.CANCELLED
    assert loaded_task.error_code == "USER_CANCELLED"
    assert loaded_station.station_id == "STATION_A"
    assert events[0].event_type is AuditEventType.TASK_CANCELLED


def test_task_repository_update_status_rejects_invalid_state_transition(tmp_path):
    db_path = tmp_path / "robot.db"
    registry = RepositoryRegistry(str(db_path))
    registry.initialize()
    task = TransportTask.create(
        source_point="STATION_A",
        destination_point="STATION_B",
        requested_by="operator-1",
        request_source="remote_dispatch",
    )
    registry.tasks.create(task)

    with pytest.raises(InvalidTaskTransition):
        registry.tasks.update_status(task.task_id, TaskStatus.COMPLETED)

    loaded_task = registry.tasks.get(task.task_id)
    assert loaded_task is not None
    assert loaded_task.status is TaskStatus.CREATED
    assert loaded_task.completed_at is None


def test_task_repository_update_status_preserves_error_code_and_previous_status(tmp_path):
    db_path = tmp_path / "robot.db"
    registry = RepositoryRegistry(str(db_path))
    registry.initialize()
    task = TransportTask.create(
        source_point="STATION_A",
        destination_point="STATION_B",
        requested_by="operator-1",
        request_source="remote_dispatch",
    )
    registry.tasks.create(task)

    updated = registry.tasks.update_status(
        task.task_id,
        TaskStatus.PAUSED,
        error_code="OPERATOR_PAUSE",
        previous_status=TaskStatus.CREATED,
    )

    assert updated.status is TaskStatus.PAUSED
    assert updated.error_code == "OPERATOR_PAUSE"
    assert updated.previous_status is TaskStatus.CREATED
