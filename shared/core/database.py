from __future__ import annotations

"""Async SQLite persistence layer for autonomous platform runtime state."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import aiosqlite

from shared.core.config import get_config
from shared.core.event_bus import Event
from shared.core.logger import get_logger


logger = get_logger(__name__)

VALID_TASK_STATUSES = {
    "queued",
    "dispatched",
    "awaiting_load",
    "in_transit",
    "awaiting_unload",
    "completed",
    "failed",
    "cancelled",
}
VALID_PRIORITIES = {0, 1, 2}
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DatabaseError(Exception):
    """Raised when database operations fail or invalid inputs are provided."""


@dataclass(frozen=True)
class TaskRecord:
    id: str
    station_id: str
    destination_id: str
    batch_id: str | None
    priority: int
    status: str
    created_at: str
    dispatched_at: str | None
    completed_at: str | None
    notes: str | None


@dataclass(frozen=True)
class TelemetryRecord:
    timestamp: str
    battery_pct: int | None
    pos_x: float | None
    pos_y: float | None
    pos_z: float | None
    roll: float | None
    pitch: float | None
    yaw: float | None
    control_mode: int | None
    connection_ok: bool


@dataclass(frozen=True)
class RouteRecord:
    id: str
    name: str
    origin_id: str
    destination_id: str
    waypoints_json: str
    active: bool
    updated_at: str


class Database:
    def __init__(self, db_path: str | Path | None = None):
        resolved_path = db_path if db_path is not None else get_config().database.sqlite_path
        self.db_path = Path(resolved_path)
        self._connection: aiosqlite.Connection | None = None
        self._lifecycle_lock = None

    async def connect(self) -> None:
        if self._lifecycle_lock is None:
            import asyncio

            self._lifecycle_lock = asyncio.Lock()

        async with self._lifecycle_lock:
            if self._connection is not None:
                return

            try:
                if not self._is_memory_database():
                    self.db_path.parent.mkdir(parents=True, exist_ok=True)
                self._connection = await aiosqlite.connect(str(self.db_path))
                self._connection.row_factory = aiosqlite.Row
                logger.info("Database connected", extra={"db_path": str(self.db_path)})
            except Exception as exc:
                logger.exception("Database connection failed", extra={"db_path": str(self.db_path)})
                raise DatabaseError(f"Failed to connect to database: {exc}") from exc

    async def close(self) -> None:
        if self._lifecycle_lock is None:
            import asyncio

            self._lifecycle_lock = asyncio.Lock()

        async with self._lifecycle_lock:
            if self._connection is None:
                return

            try:
                await self._connection.close()
                logger.info("Database closed", extra={"db_path": str(self.db_path)})
            except Exception as exc:
                logger.exception("Database close failed", extra={"db_path": str(self.db_path)})
                raise DatabaseError(f"Failed to close database: {exc}") from exc
            finally:
                self._connection = None

    async def initialize(self) -> None:
        await self.connect()
        connection = await self._ensure_connected()
        try:
            await connection.execute("PRAGMA journal_mode=WAL;")
            await connection.execute("PRAGMA foreign_keys=ON;")
            await connection.execute("PRAGMA busy_timeout=5000;")
            await connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    station_id TEXT NOT NULL,
                    destination_id TEXT NOT NULL,
                    batch_id TEXT,
                    priority INTEGER NOT NULL DEFAULT 0 CHECK (priority IN ({", ".join(str(item) for item in sorted(VALID_PRIORITIES))})),
                    status TEXT NOT NULL CHECK (status IN ({", ".join(f"'{item}'" for item in sorted(VALID_TASK_STATUSES))})),
                    created_at TEXT NOT NULL,
                    dispatched_at TEXT,
                    completed_at TEXT,
                    notes TEXT
                );

                CREATE TABLE IF NOT EXISTS quadruped_telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    battery_pct INTEGER,
                    pos_x REAL,
                    pos_y REAL,
                    pos_z REAL,
                    roll REAL,
                    pitch REAL,
                    yaw REAL,
                    control_mode INTEGER,
                    connection_ok INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    source TEXT,
                    task_id TEXT,
                    correlation_id TEXT
                );

                CREATE TABLE IF NOT EXISTS routes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    origin_id TEXT NOT NULL,
                    destination_id TEXT NOT NULL,
                    waypoints_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
                """
            )
            await connection.commit()
            logger.info("Database initialized", extra={"db_path": str(self.db_path)})
        except Exception as exc:
            logger.exception("Database initialization failed", extra={"db_path": str(self.db_path)})
            raise DatabaseError(f"Failed to initialize database: {exc}") from exc

    async def is_connected(self) -> bool:
        return self._connection is not None

    async def create_task(
        self,
        station_id: str,
        destination_id: str,
        batch_id: str | None = None,
        priority: int = 0,
        notes: str | None = None,
        task_id: str | None = None,
    ) -> TaskRecord:
        self._validate_non_empty("station_id", station_id)
        self._validate_non_empty("destination_id", destination_id)
        self._validate_priority(priority)

        created_task = TaskRecord(
            id=task_id or str(uuid4()),
            station_id=station_id.strip(),
            destination_id=destination_id.strip(),
            batch_id=batch_id,
            priority=priority,
            status="queued",
            created_at=utc_now_iso(),
            dispatched_at=None,
            completed_at=None,
            notes=notes,
        )
        connection = await self._ensure_connected()
        try:
            await connection.execute(
                """
                INSERT INTO tasks (
                    id, station_id, destination_id, batch_id, priority, status,
                    created_at, dispatched_at, completed_at, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_task.id,
                    created_task.station_id,
                    created_task.destination_id,
                    created_task.batch_id,
                    created_task.priority,
                    created_task.status,
                    created_task.created_at,
                    created_task.dispatched_at,
                    created_task.completed_at,
                    created_task.notes,
                ),
            )
            await connection.commit()
            logger.debug("Task created", extra={"task_id": created_task.id})
            return created_task
        except Exception as exc:
            logger.exception("Task creation failed", extra={"task_id": created_task.id})
            raise DatabaseError(f"Failed to create task: {exc}") from exc

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        notes: str | None = None,
    ) -> TaskRecord:
        normalized_status = self._validate_status(status)
        existing = await self.get_task(task_id)
        if existing is None:
            raise DatabaseError(f"Task not found: {task_id}")

        dispatched_at = existing.dispatched_at
        completed_at = existing.completed_at
        if normalized_status == "dispatched" and dispatched_at is None:
            dispatched_at = utc_now_iso()
        if normalized_status in TERMINAL_TASK_STATUSES and completed_at is None:
            completed_at = utc_now_iso()
        updated_notes = notes if notes is not None else existing.notes

        connection = await self._ensure_connected()
        try:
            await connection.execute(
                """
                UPDATE tasks
                SET status = ?, dispatched_at = ?, completed_at = ?, notes = ?
                WHERE id = ?
                """,
                (normalized_status, dispatched_at, completed_at, updated_notes, task_id),
            )
            await connection.commit()
            updated = await self.get_task(task_id)
            if updated is None:
                raise DatabaseError(f"Task not found after update: {task_id}")
            logger.debug("Task status updated", extra={"task_id": task_id})
            return updated
        except DatabaseError:
            raise
        except Exception as exc:
            logger.exception("Task status update failed", extra={"task_id": task_id})
            raise DatabaseError(f"Failed to update task status: {exc}") from exc

    async def get_task(self, task_id: str) -> TaskRecord | None:
        connection = await self._ensure_connected()
        try:
            cursor = await connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = await cursor.fetchone()
            return self._row_to_task_record(row) if row is not None else None
        except Exception as exc:
            raise DatabaseError(f"Failed to get task: {exc}") from exc

    async def get_queued_tasks(self) -> list[TaskRecord]:
        connection = await self._ensure_connected()
        try:
            cursor = await connection.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'queued'
                ORDER BY priority DESC, created_at ASC
                """
            )
            rows = await cursor.fetchall()
            return [self._row_to_task_record(row) for row in rows]
        except Exception as exc:
            raise DatabaseError(f"Failed to get queued tasks: {exc}") from exc

    async def list_tasks(self, status: str | None = None) -> list[TaskRecord]:
        connection = await self._ensure_connected()
        try:
            if status is not None:
                normalized_status = self._validate_status(status)
                cursor = await connection.execute(
                    "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC",
                    (normalized_status,),
                )
            else:
                cursor = await connection.execute("SELECT * FROM tasks ORDER BY created_at DESC")
            rows = await cursor.fetchall()
            return [self._row_to_task_record(row) for row in rows]
        except DatabaseError:
            raise
        except Exception as exc:
            raise DatabaseError(f"Failed to list tasks: {exc}") from exc

    async def log_telemetry(
        self,
        battery_pct: int | None = None,
        pos_x: float | None = None,
        pos_y: float | None = None,
        pos_z: float | None = None,
        roll: float | None = None,
        pitch: float | None = None,
        yaw: float | None = None,
        control_mode: int | None = None,
        connection_ok: bool = True,
    ) -> None:
        record = TelemetryRecord(
            timestamp=utc_now_iso(),
            battery_pct=battery_pct,
            pos_x=pos_x,
            pos_y=pos_y,
            pos_z=pos_z,
            roll=roll,
            pitch=pitch,
            yaw=yaw,
            control_mode=control_mode,
            connection_ok=connection_ok,
        )
        connection = await self._ensure_connected()
        try:
            await connection.execute(
                """
                INSERT INTO quadruped_telemetry (
                    timestamp, battery_pct, pos_x, pos_y, pos_z,
                    roll, pitch, yaw, control_mode, connection_ok
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.timestamp,
                    record.battery_pct,
                    record.pos_x,
                    record.pos_y,
                    record.pos_z,
                    record.roll,
                    record.pitch,
                    record.yaw,
                    record.control_mode,
                    1 if record.connection_ok else 0,
                ),
            )
            await connection.commit()
            logger.debug("Telemetry logged")
        except Exception as exc:
            logger.exception("Telemetry logging failed")
            raise DatabaseError(f"Failed to log telemetry: {exc}") from exc

    async def prune_old_telemetry(self, retention_hours: int | None = None) -> int:
        hours = retention_hours if retention_hours is not None else get_config().database.telemetry_retention_hours
        if hours <= 0:
            raise DatabaseError("retention_hours must be positive")

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        connection = await self._ensure_connected()
        try:
            cursor = await connection.execute(
                "DELETE FROM quadruped_telemetry WHERE timestamp < ?",
                (cutoff,),
            )
            await connection.commit()
            return cursor.rowcount if cursor.rowcount != -1 else 0
        except Exception as exc:
            logger.exception("Telemetry prune failed")
            raise DatabaseError(f"Failed to prune telemetry: {exc}") from exc

    async def log_event(
        self,
        event_name: str,
        payload: dict[str, Any] | None = None,
        source: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
        event_id: str | None = None,
    ) -> str:
        normalized_event_name = event_name.strip()
        if not normalized_event_name:
            raise DatabaseError("event_name must not be empty")

        created_event_id = event_id or str(uuid4())
        payload_json = json.dumps(payload or {})
        connection = await self._ensure_connected()
        try:
            await connection.execute(
                """
                INSERT INTO events (
                    id, timestamp, event_name, payload_json, source, task_id, correlation_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_event_id,
                    utc_now_iso(),
                    normalized_event_name,
                    payload_json,
                    source,
                    task_id,
                    correlation_id,
                ),
            )
            await connection.commit()
            logger.debug("Event logged", extra={"event_id": created_event_id})
            return created_event_id
        except Exception as exc:
            logger.exception("Event logging failed", extra={"event_id": created_event_id})
            raise DatabaseError(f"Failed to log event: {exc}") from exc

    async def log_bus_event(self, event: Event) -> str:
        connection = await self._ensure_connected()
        try:
            await connection.execute(
                """
                INSERT INTO events (
                    id, timestamp, event_name, payload_json, source, task_id, correlation_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.timestamp.isoformat(),
                    event.name.value if hasattr(event.name, "value") else str(event.name),
                    json.dumps(event.payload or {}),
                    event.source,
                    event.task_id,
                    event.correlation_id,
                ),
            )
            await connection.commit()
            logger.debug("Bus event logged", extra={"event_id": event.event_id})
            return event.event_id
        except Exception as exc:
            logger.exception("Bus event logging failed", extra={"event_id": event.event_id})
            raise DatabaseError(f"Failed to log bus event: {exc}") from exc

    async def list_events(
        self,
        event_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise DatabaseError("limit must be between 1 and 1000")
        if offset < 0:
            raise DatabaseError("offset must be >= 0")

        connection = await self._ensure_connected()
        try:
            if event_name is not None:
                cursor = await connection.execute(
                    """
                    SELECT * FROM events
                    WHERE event_name = ?
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?
                    """,
                    (event_name, limit, offset),
                )
            else:
                cursor = await connection.execute(
                    "SELECT * FROM events ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            rows = await cursor.fetchall()
            return [
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "event_name": row["event_name"],
                    "payload": json.loads(row["payload_json"]),
                    "source": row["source"],
                    "task_id": row["task_id"],
                    "correlation_id": row["correlation_id"],
                }
                for row in rows
            ]
        except DatabaseError:
            raise
        except Exception as exc:
            raise DatabaseError(f"Failed to list events: {exc}") from exc

    async def upsert_route(
        self,
        name: str,
        origin_id: str,
        destination_id: str,
        waypoints: list[dict[str, Any]],
        active: bool = True,
        route_id: str | None = None,
    ) -> RouteRecord:
        normalized_name = self._validate_non_empty("name", name)
        normalized_origin = self._validate_non_empty("origin_id", origin_id)
        normalized_destination = self._validate_non_empty("destination_id", destination_id)
        waypoints_json = json.dumps(waypoints)
        updated_at = utc_now_iso()
        connection = await self._ensure_connected()

        try:
            existing = await self._get_route_by_name(normalized_name)
            resolved_route_id = (
                existing.id
                if existing is not None
                else route_id or str(uuid5(NAMESPACE_URL, f"{normalized_origin}:{normalized_destination}:{normalized_name}"))
            )
            if existing is None:
                await connection.execute(
                    """
                    INSERT INTO routes (
                        id, name, origin_id, destination_id, waypoints_json, active, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_route_id,
                        normalized_name,
                        normalized_origin,
                        normalized_destination,
                        waypoints_json,
                        1 if active else 0,
                        updated_at,
                    ),
                )
            else:
                await connection.execute(
                    """
                    UPDATE routes
                    SET origin_id = ?, destination_id = ?, waypoints_json = ?, active = ?, updated_at = ?
                    WHERE name = ?
                    """,
                    (
                        normalized_origin,
                        normalized_destination,
                        waypoints_json,
                        1 if active else 0,
                        updated_at,
                        normalized_name,
                    ),
                )
            await connection.commit()
            route = await self._get_route_by_name(normalized_name)
            if route is None:
                raise DatabaseError(f"Route not found after upsert: {normalized_name}")
            logger.debug("Route upserted", extra={"route_name": normalized_name})
            return route
        except DatabaseError:
            raise
        except Exception as exc:
            logger.exception("Route upsert failed", extra={"route_name": normalized_name})
            raise DatabaseError(f"Failed to upsert route: {exc}") from exc

    async def get_route(
        self,
        origin_id: str,
        destination_id: str,
    ) -> RouteRecord | None:
        normalized_origin = self._validate_non_empty("origin_id", origin_id)
        normalized_destination = self._validate_non_empty("destination_id", destination_id)
        connection = await self._ensure_connected()
        try:
            cursor = await connection.execute(
                """
                SELECT * FROM routes
                WHERE origin_id = ? AND destination_id = ? AND active = 1
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (normalized_origin, normalized_destination),
            )
            row = await cursor.fetchone()
            return self._row_to_route_record(row) if row is not None else None
        except DatabaseError:
            raise
        except Exception as exc:
            raise DatabaseError(f"Failed to get route: {exc}") from exc

    async def list_routes(self, active: bool | None = None) -> list[RouteRecord]:
        connection = await self._ensure_connected()
        try:
            if active is None:
                cursor = await connection.execute("SELECT * FROM routes ORDER BY updated_at DESC")
            else:
                cursor = await connection.execute(
                    "SELECT * FROM routes WHERE active = ? ORDER BY updated_at DESC",
                    (1 if active else 0,),
                )
            rows = await cursor.fetchall()
            return [self._row_to_route_record(row) for row in rows]
        except Exception as exc:
            raise DatabaseError(f"Failed to list routes: {exc}") from exc

    async def set_route_active(self, name: str, active: bool) -> RouteRecord:
        normalized_name = self._validate_non_empty("name", name)
        route = await self._get_route_by_name(normalized_name)
        if route is None:
            raise DatabaseError(f"Route not found: {normalized_name}")

        connection = await self._ensure_connected()
        try:
            await connection.execute(
                "UPDATE routes SET active = ?, updated_at = ? WHERE name = ?",
                (1 if active else 0, utc_now_iso(), normalized_name),
            )
            await connection.commit()
            updated = await self._get_route_by_name(normalized_name)
            if updated is None:
                raise DatabaseError(f"Route not found after update: {normalized_name}")
            logger.debug("Route active flag updated", extra={"route_name": normalized_name})
            return updated
        except DatabaseError:
            raise
        except Exception as exc:
            logger.exception("Route activation update failed", extra={"route_name": normalized_name})
            raise DatabaseError(f"Failed to update route active flag: {exc}") from exc

    async def _ensure_connected(self) -> aiosqlite.Connection:
        if self._connection is None:
            await self.connect()
        if self._connection is None:
            raise DatabaseError("Database connection is not available")
        return self._connection

    def _is_memory_database(self) -> bool:
        return str(self.db_path) == ":memory:"

    def _validate_priority(self, priority: int) -> int:
        if priority not in VALID_PRIORITIES:
            raise DatabaseError(f"priority must be one of {sorted(VALID_PRIORITIES)}")
        return priority

    def _validate_status(self, status: str) -> str:
        normalized_status = status.strip()
        if normalized_status not in VALID_TASK_STATUSES:
            raise DatabaseError(f"status must be one of {sorted(VALID_TASK_STATUSES)}")
        return normalized_status

    def _validate_non_empty(self, field_name: str, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise DatabaseError(f"{field_name} must not be empty")
        return normalized_value

    async def _get_route_by_name(self, name: str) -> RouteRecord | None:
        connection = await self._ensure_connected()
        cursor = await connection.execute("SELECT * FROM routes WHERE name = ?", (name,))
        row = await cursor.fetchone()
        return self._row_to_route_record(row) if row is not None else None

    def _row_to_task_record(self, row: aiosqlite.Row) -> TaskRecord:
        return TaskRecord(
            id=row["id"],
            station_id=row["station_id"],
            destination_id=row["destination_id"],
            batch_id=row["batch_id"],
            priority=row["priority"],
            status=row["status"],
            created_at=row["created_at"],
            dispatched_at=row["dispatched_at"],
            completed_at=row["completed_at"],
            notes=row["notes"],
        )

    def _row_to_route_record(self, row: aiosqlite.Row) -> RouteRecord:
        return RouteRecord(
            id=row["id"],
            name=row["name"],
            origin_id=row["origin_id"],
            destination_id=row["destination_id"],
            waypoints_json=row["waypoints_json"],
            active=bool(row["active"]),
            updated_at=row["updated_at"],
        )


database = Database()


def get_database() -> Database:
    return database


__all__ = [
    "Database",
    "DatabaseError",
    "RouteRecord",
    "TaskRecord",
    "TelemetryRecord",
    "VALID_PRIORITIES",
    "VALID_TASK_STATUSES",
    "database",
    "get_database",
    "utc_now_iso",
]
