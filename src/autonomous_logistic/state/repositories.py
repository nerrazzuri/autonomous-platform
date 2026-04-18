from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from autonomous_logistic.core.models import AuditEvent, AuditEventType, Station, TaskStatus, TransportTask
from autonomous_logistic.state.task_state_machine import transition_status


def encode_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def decode_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def encode_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True)


def decode_json(value: str | None) -> dict[str, Any]:
    return json.loads(value) if value else {}


class RepositoryRegistry:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.tasks = TaskRepository(db_path)
        self.stations = StationRepository(db_path)
        self.events = AuditEventRepository(db_path)

    def initialize(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    source_point TEXT NOT NULL,
                    destination_point TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    request_source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    notes TEXT,
                    previous_status TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS stations (
                    station_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    position_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    task_id TEXT,
                    metadata_json TEXT NOT NULL
                )
                """
            )


class TaskRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def create(self, task: TransportTask) -> TransportTask:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, source_point, destination_point, requested_by, request_source,
                    created_at, started_at, completed_at, status, error_code, notes, previous_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._to_row(task),
            )
        return task

    def get(self, task_id: str) -> TransportTask | None:
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._from_row(row) if row else None

    def list_all(self) -> list[TransportTask]:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute("SELECT * FROM tasks ORDER BY created_at ASC").fetchall()
        return [self._from_row(row) for row in rows]

    def save(self, task: TransportTask) -> TransportTask:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE tasks
                SET source_point = ?, destination_point = ?, requested_by = ?, request_source = ?,
                    created_at = ?, started_at = ?, completed_at = ?, status = ?,
                    error_code = ?, notes = ?, previous_status = ?
                WHERE task_id = ?
                """,
                (
                    task.source_point,
                    task.destination_point,
                    task.requested_by,
                    task.request_source,
                    encode_datetime(task.created_at),
                    encode_datetime(task.started_at),
                    encode_datetime(task.completed_at),
                    task.status.value,
                    task.error_code,
                    task.notes,
                    task.previous_status.value if task.previous_status else None,
                    task.task_id,
                ),
            )
        return task

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        error_code: str | None = None,
        previous_status: TaskStatus | None = None,
    ) -> TransportTask:
        task = self.get(task_id)
        if task is None:
            raise KeyError(task_id)
        task.status = transition_status(task.status, status)
        task.error_code = error_code
        task.previous_status = previous_status
        if status is TaskStatus.COMPLETED:
            from autonomous_logistic.core.models import utc_now

            task.completed_at = utc_now()
        return self.save(task)

    def _to_row(self, task: TransportTask) -> tuple[Any, ...]:
        return (
            task.task_id,
            task.source_point,
            task.destination_point,
            task.requested_by,
            task.request_source,
            encode_datetime(task.created_at),
            encode_datetime(task.started_at),
            encode_datetime(task.completed_at),
            task.status.value,
            task.error_code,
            task.notes,
            task.previous_status.value if task.previous_status else None,
        )

    def _from_row(self, row: tuple[Any, ...]) -> TransportTask:
        return TransportTask(
            task_id=row[0],
            source_point=row[1],
            destination_point=row[2],
            requested_by=row[3],
            request_source=row[4],
            created_at=decode_datetime(row[5]),
            started_at=decode_datetime(row[6]),
            completed_at=decode_datetime(row[7]),
            status=TaskStatus(row[8]),
            error_code=row[9],
            notes=row[10],
            previous_status=TaskStatus(row[11]) if row[11] else None,
        )


class StationRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def upsert(self, station: Station) -> Station:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO stations (station_id, name, position_json, metadata_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(station_id) DO UPDATE SET
                    name = excluded.name,
                    position_json = excluded.position_json,
                    metadata_json = excluded.metadata_json
                """,
                (station.station_id, station.name, encode_json(station.position), encode_json(station.metadata)),
            )
        return station

    def list_all(self) -> list[Station]:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute("SELECT station_id, name, position_json, metadata_json FROM stations ORDER BY station_id ASC").fetchall()
        return [
            Station(
                station_id=row[0],
                name=row[1],
                position=decode_json(row[2]),
                metadata=decode_json(row[3]),
            )
            for row in rows
        ]


class AuditEventRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def create(self, event: AuditEvent) -> AuditEvent:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO audit_events (event_id, event_type, created_at, task_id, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_type.value,
                    encode_datetime(event.created_at),
                    event.task_id,
                    encode_json(event.metadata),
                ),
            )
        return event

    def list_for_task(self, task_id: str) -> list[AuditEvent]:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT event_id, event_type, created_at, task_id, metadata_json FROM audit_events WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def _from_row(self, row: tuple[Any, ...]) -> AuditEvent:
        return AuditEvent(
            event_id=row[0],
            event_type=AuditEventType(row[1]),
            created_at=decode_datetime(row[2]),
            task_id=row[3],
            metadata=decode_json(row[4]),
        )
