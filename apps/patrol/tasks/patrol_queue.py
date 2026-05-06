from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from shared.core.database import Database, DatabaseError, get_database, utc_now_iso
from apps.patrol import events as patrol_events
from shared.core.event_bus import EventName, get_event_bus
from shared.core.logger import get_logger

from apps.patrol.tasks.patrol_record import (
    InvalidCycleTransition,
    PatrolCycleStateMachine,
    PatrolCycleStatus,
    PatrolRecord,
    PatrolTaskError,
)


logger = get_logger(__name__)

PATROL_CYCLE_STATUSES = (
    PatrolCycleStatus.SCHEDULED.value,
    PatrolCycleStatus.ACTIVE.value,
    PatrolCycleStatus.COMPLETED.value,
    PatrolCycleStatus.FAILED.value,
    PatrolCycleStatus.SUSPENDED.value,
)


class PatrolQueueError(Exception):
    """Raised when patrol queue operations fail validation or persistence."""


class PatrolCycleNotFound(PatrolQueueError):
    """Raised when a patrol cycle cannot be found."""


class PatrolQueue:
    def __init__(self, database: Database | None = None) -> None:
        self._database = database or get_database()

        import asyncio

        self._lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return

            try:
                await self._database.initialize()
                connection = await self._database._ensure_connected()
                await connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS patrol_cycles (
                        cycle_id TEXT PRIMARY KEY,
                        route_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        triggered_by TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT,
                        waypoints_total INTEGER NOT NULL,
                        waypoints_observed INTEGER NOT NULL,
                        anomaly_ids_json TEXT NOT NULL,
                        failure_reason TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_patrol_cycles_status
                    ON patrol_cycles (status);

                    CREATE INDEX IF NOT EXISTS idx_patrol_cycles_created_at
                    ON patrol_cycles (created_at);
                    """
                )
                await connection.commit()
            except DatabaseError:
                logger.exception("Patrol queue initialization failed")
                raise
            except Exception as exc:
                logger.exception("Patrol queue initialization failed")
                raise PatrolQueueError(f"Failed to initialize patrol queue: {exc}") from exc

            self._initialized = True

    async def submit_cycle(
        self,
        route_id: str,
        triggered_by: str = "manual",
        cycle_id: str | None = None,
        waypoints_total: int = 0,
    ) -> PatrolRecord:
        route_id = self._validate_non_empty("route_id", route_id)
        cycle_identifier = cycle_id or str(uuid4())
        await self.initialize()

        try:
            record = PatrolRecord(
                cycle_id=cycle_identifier,
                route_id=route_id,
                status=PatrolCycleStatus.SCHEDULED,
                triggered_by=triggered_by,
                created_at=utc_now_iso(),
                waypoints_total=waypoints_total,
            )
        except PatrolTaskError as exc:
            raise PatrolQueueError(str(exc)) from exc

        async with self._lock:
            connection = await self._get_connection()
            try:
                await connection.execute(
                    """
                    INSERT INTO patrol_cycles (
                        cycle_id, route_id, status, triggered_by, created_at, started_at,
                        completed_at, waypoints_total, waypoints_observed, anomaly_ids_json, failure_reason
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._record_to_row_values(record),
                )
                await connection.commit()
            except Exception as exc:
                logger.exception("Patrol cycle submit failed", extra={"cycle_id": cycle_identifier, "route_id": route_id})
                raise PatrolQueueError(f"Failed to submit patrol cycle: {exc}") from exc

        logger.info("Patrol cycle submitted", extra={"cycle_id": record.cycle_id, "route_id": record.route_id})
        return record

    async def get_cycle(self, cycle_id: str) -> PatrolRecord:
        cycle_id = self._validate_non_empty("cycle_id", cycle_id)
        await self.initialize()
        connection = await self._get_connection()
        try:
            cursor = await connection.execute("SELECT * FROM patrol_cycles WHERE cycle_id = ?", (cycle_id,))
            row = await cursor.fetchone()
        except Exception as exc:
            logger.exception("Patrol cycle fetch failed", extra={"cycle_id": cycle_id})
            raise PatrolQueueError(f"Failed to get patrol cycle: {exc}") from exc

        if row is None:
            raise PatrolCycleNotFound(f"Patrol cycle not found: {cycle_id}")
        return self._row_to_record(row)

    async def get_next_cycle(self) -> PatrolRecord | None:
        await self.initialize()
        connection = await self._get_connection()
        try:
            cursor = await connection.execute(
                """
                SELECT * FROM patrol_cycles
                WHERE status = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (PatrolCycleStatus.SCHEDULED.value,),
            )
            row = await cursor.fetchone()
        except Exception as exc:
            logger.exception("Patrol next cycle fetch failed")
            raise PatrolQueueError(f"Failed to get next patrol cycle: {exc}") from exc
        return self._row_to_record(row) if row is not None else None

    async def mark_active(self, cycle_id: str) -> PatrolRecord:
        return await self._transition_cycle(
            cycle_id,
            PatrolCycleStatus.ACTIVE,
            event_name=patrol_events.PATROL_CYCLE_STARTED,
            updates=lambda current: {
                "started_at": current.started_at or utc_now_iso(),
            },
        )

    async def mark_completed(self, cycle_id: str, stats_dict: dict[str, Any] | None = None) -> PatrolRecord:
        stats = dict(stats_dict or {})
        return await self._transition_cycle(
            cycle_id,
            PatrolCycleStatus.COMPLETED,
            event_name=patrol_events.PATROL_CYCLE_COMPLETED,
            updates=lambda current: {
                "completed_at": utc_now_iso(),
                "waypoints_total": stats.get("waypoints_total", current.waypoints_total),
                "waypoints_observed": stats.get("waypoints_observed", current.waypoints_observed),
                "anomaly_ids": stats.get("anomaly_ids", current.anomaly_ids),
            },
        )

    async def mark_failed(self, cycle_id: str, reason: str) -> PatrolRecord:
        reason = self._validate_non_empty("reason", reason)
        return await self._transition_cycle(
            cycle_id,
            PatrolCycleStatus.FAILED,
            event_name=patrol_events.PATROL_CYCLE_FAILED,
            updates=lambda _current: {
                "completed_at": utc_now_iso(),
                "failure_reason": reason,
            },
        )

    async def suspend_cycle(self, cycle_id: str, reason: str | None = None) -> PatrolRecord:
        if reason is not None:
            reason = self._validate_non_empty("reason", reason)
        return await self._transition_cycle(
            cycle_id,
            PatrolCycleStatus.SUSPENDED,
            event_name=patrol_events.PATROL_SUSPENDED,
            updates=lambda current: {
                "failure_reason": reason if reason is not None else current.failure_reason,
            },
        )

    async def resume_cycle(self, cycle_id: str) -> PatrolRecord:
        return await self._transition_cycle(
            cycle_id,
            PatrolCycleStatus.ACTIVE,
            event_name=patrol_events.PATROL_RESUMED,
            updates=lambda current: {
                "started_at": current.started_at or utc_now_iso(),
            },
        )

    async def get_queue_status(self) -> dict[str, int]:
        await self.initialize()
        connection = await self._get_connection()
        counts = {status: 0 for status in PATROL_CYCLE_STATUSES}
        try:
            cursor = await connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM patrol_cycles
                GROUP BY status
                """
            )
            rows = await cursor.fetchall()
        except Exception as exc:
            logger.exception("Patrol queue status query failed")
            raise PatrolQueueError(f"Failed to get patrol queue status: {exc}") from exc

        total = 0
        for row in rows:
            status = row["status"]
            count = int(row["count"])
            counts[status] = count
            total += count

        return {
            "scheduled": counts[PatrolCycleStatus.SCHEDULED.value],
            "active": counts[PatrolCycleStatus.ACTIVE.value],
            "completed": counts[PatrolCycleStatus.COMPLETED.value],
            "failed": counts[PatrolCycleStatus.FAILED.value],
            "suspended": counts[PatrolCycleStatus.SUSPENDED.value],
            "total": total,
        }

    async def get_cycle_history(self, limit: int = 100) -> list[PatrolRecord]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 1000:
            raise PatrolQueueError("limit must be between 1 and 1000")

        await self.initialize()
        connection = await self._get_connection()
        try:
            cursor = await connection.execute(
                "SELECT * FROM patrol_cycles ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        except Exception as exc:
            logger.exception("Patrol cycle history query failed")
            raise PatrolQueueError(f"Failed to get patrol cycle history: {exc}") from exc
        return [self._row_to_record(row) for row in rows]

    async def _transition_cycle(
        self,
        cycle_id: str,
        new_status: PatrolCycleStatus,
        *,
        event_name: EventName | str,
        updates,
    ) -> PatrolRecord:
        cycle_id = self._validate_non_empty("cycle_id", cycle_id)
        await self.initialize()

        async with self._lock:
            current = await self.get_cycle(cycle_id)
            try:
                PatrolCycleStateMachine.transition_status(current.status, new_status)
            except InvalidCycleTransition:
                logger.warning(
                    "Invalid patrol cycle transition",
                    extra={"cycle_id": cycle_id, "current": current.status.value, "new": new_status.value},
                )
                raise

            merged_data = current.to_dict()
            merged_data["status"] = new_status
            merged_data.update(dict(updates(current)))

            try:
                updated_record = PatrolRecord.from_dict(merged_data)
            except PatrolTaskError as exc:
                raise PatrolQueueError(str(exc)) from exc

            connection = await self._get_connection()
            try:
                await connection.execute(
                    """
                    UPDATE patrol_cycles
                    SET status = ?, started_at = ?, completed_at = ?, waypoints_total = ?,
                        waypoints_observed = ?, anomaly_ids_json = ?, failure_reason = ?
                    WHERE cycle_id = ?
                    """,
                    (
                        updated_record.status.value,
                        updated_record.started_at,
                        updated_record.completed_at,
                        updated_record.waypoints_total,
                        updated_record.waypoints_observed,
                        json.dumps(updated_record.anomaly_ids),
                        updated_record.failure_reason,
                        updated_record.cycle_id,
                    ),
                )
                await connection.commit()
            except Exception as exc:
                logger.exception(
                    "Patrol cycle update failed",
                    extra={"cycle_id": cycle_id, "new_status": new_status.value},
                )
                raise PatrolQueueError(f"Failed to update patrol cycle: {exc}") from exc

        logger.info(
            "Patrol cycle transitioned",
            extra={"cycle_id": updated_record.cycle_id, "from_status": current.status.value, "to_status": new_status.value},
        )
        self._publish_event(event_name, updated_record)
        return updated_record

    async def _get_connection(self):
        try:
            return await self._database._ensure_connected()
        except DatabaseError as exc:
            logger.exception("Patrol queue database connection unavailable")
            raise PatrolQueueError(str(exc)) from exc

    def _row_to_record(self, row) -> PatrolRecord:
        return PatrolRecord.from_dict(
            {
                "cycle_id": row["cycle_id"],
                "route_id": row["route_id"],
                "status": row["status"],
                "triggered_by": row["triggered_by"],
                "created_at": row["created_at"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "waypoints_total": row["waypoints_total"],
                "waypoints_observed": row["waypoints_observed"],
                "anomaly_ids": json.loads(row["anomaly_ids_json"]),
                "failure_reason": row["failure_reason"],
            }
        )

    def _record_to_row_values(self, record: PatrolRecord) -> tuple[Any, ...]:
        return (
            record.cycle_id,
            record.route_id,
            record.status.value,
            record.triggered_by,
            record.created_at,
            record.started_at,
            record.completed_at,
            record.waypoints_total,
            record.waypoints_observed,
            json.dumps(record.anomaly_ids),
            record.failure_reason,
        )

    def _publish_event(self, event_name: EventName | str, record: PatrolRecord) -> None:
        try:
            get_event_bus().publish_nowait(
                event_name,
                payload=self._event_payload(record),
                source=__name__,
                task_id=record.cycle_id,
            )
        except Exception:
            logger.debug("Patrol queue event publish skipped", extra={"event_name": event_name.value})

    def _event_payload(self, record: PatrolRecord) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "cycle_id": record.cycle_id,
            "route_id": record.route_id,
            "status": record.status.value,
            "triggered_by": record.triggered_by,
            "created_at": record.created_at,
            "waypoints_total": record.waypoints_total,
            "waypoints_observed": record.waypoints_observed,
            "anomaly_ids": list(record.anomaly_ids),
        }
        if record.started_at is not None:
            payload["started_at"] = record.started_at
        if record.completed_at is not None:
            payload["completed_at"] = record.completed_at
        if record.failure_reason is not None:
            payload["failure_reason"] = record.failure_reason
        return payload

    def _validate_non_empty(self, field_name: str, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise PatrolQueueError(f"{field_name} must not be empty")
        return value.strip()


__all__ = [
    "PatrolCycleNotFound",
    "PatrolQueue",
    "PatrolQueueError",
]
