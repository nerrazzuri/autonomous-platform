from __future__ import annotations

"""Business-safe task queue wrapper around the database task records."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from core.config import get_config
from core.database import Database, DatabaseError, TaskRecord, get_database
from core.event_bus import EventName, get_event_bus
from core.logger import get_logger


logger = get_logger(__name__)

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"dispatched", "failed", "cancelled"},
    "dispatched": {"awaiting_load", "failed", "cancelled"},
    "awaiting_load": {"in_transit", "failed", "cancelled"},
    "in_transit": {"awaiting_unload", "failed"},
    "awaiting_unload": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}

ALL_TASK_STATUSES = (
    "queued",
    "dispatched",
    "awaiting_load",
    "in_transit",
    "awaiting_unload",
    "completed",
    "failed",
    "cancelled",
)


class TaskQueueError(Exception):
    """Raised when queue operations fail validation or cannot complete safely."""


class InvalidTaskTransitionError(TaskQueueError):
    """Raised when a task lifecycle transition is not allowed."""


@dataclass(frozen=True)
class QueueSummary:
    total: int
    queued: int
    dispatched: int
    awaiting_load: int
    in_transit: int
    awaiting_unload: int
    completed: int
    failed: int
    cancelled: int


@dataclass(frozen=True)
class ScoredTask:
    task: TaskRecord
    score: float
    components: dict[str, float]


class TaskQueue:
    """Queue lifecycle and scoring logic for quadruped logistics tasks."""

    def __init__(
        self,
        database: Database | None = None,
        priority_weight: float | None = None,
        recency_weight: float | None = None,
        proximity_weight: float | None = None,
        direction_bonus: float | None = None,
    ) -> None:
        config = get_config()
        self._database = database or get_database()
        self._priority_weight = (
            config.task_scoring.priority_weight if priority_weight is None else priority_weight
        )
        self._recency_weight = config.task_scoring.recency_weight if recency_weight is None else recency_weight
        self._proximity_weight = (
            config.task_scoring.proximity_weight if proximity_weight is None else proximity_weight
        )
        self._direction_bonus = config.task_scoring.direction_bonus if direction_bonus is None else direction_bonus
        for field_name, value in (
            ("priority_weight", self._priority_weight),
            ("recency_weight", self._recency_weight),
            ("proximity_weight", self._proximity_weight),
            ("direction_bonus", self._direction_bonus),
        ):
            if value < 0:
                raise TaskQueueError(f"{field_name} must be >= 0")

        import asyncio

        self._lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._initialized = False
        self._last_completed_destination_id: str | None = None

    async def submit_task(
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
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 0:
            raise TaskQueueError("priority must be a non-negative integer")

        async with self._lock:
            await self._ensure_initialized()
            resolved_task_id = task_id or str(uuid4())
            try:
                task = await self._database.create_task(
                    station_id=station_id,
                    destination_id=destination_id,
                    batch_id=batch_id,
                    priority=priority,
                    notes=notes,
                    task_id=resolved_task_id,
                )
            except DatabaseError as exc:
                raise TaskQueueError(str(exc)) from exc

        self._publish_event(EventName.TASK_SUBMITTED, self._event_payload(task), task_id=task.id)
        logger.info("Task submitted", extra={"task_id": task.id, "priority": task.priority})
        return task

    async def get_task(self, task_id: str) -> TaskRecord:
        self._validate_non_empty("task_id", task_id)
        await self._ensure_initialized()
        try:
            task = await self._database.get_task(task_id)
        except DatabaseError as exc:
            raise TaskQueueError(str(exc)) from exc
        if task is None:
            raise TaskQueueError(f"Task not found: {task_id}")
        return task

    async def list_tasks(self, status: str | None = None, limit: int = 100, offset: int = 0) -> list[TaskRecord]:
        if limit < 1 or limit > 1000:
            raise TaskQueueError("limit must be between 1 and 1000")
        if offset < 0:
            raise TaskQueueError("offset must be >= 0")

        await self._ensure_initialized()
        try:
            tasks = await self._database.list_tasks(status=status)
        except DatabaseError as exc:
            raise TaskQueueError(str(exc)) from exc
        return tasks[offset : offset + limit]

    async def get_queue_status(self) -> QueueSummary:
        tasks = await self.list_tasks(limit=1000, offset=0)
        return self._build_summary(tasks)

    async def get_next_task(self, robot_position: tuple[float, float] | None = None) -> TaskRecord | None:
        candidates = await self.get_scored_candidates(robot_position=robot_position)
        return candidates[0].task if candidates else None

    async def get_scored_candidates(self, robot_position: tuple[float, float] | None = None) -> list[ScoredTask]:
        await self._ensure_initialized()
        try:
            queued_tasks = await self._database.get_queued_tasks()
        except DatabaseError as exc:
            raise TaskQueueError(str(exc)) from exc

        scored_tasks = [self._score_task(task, robot_position=robot_position) for task in queued_tasks]
        scored_tasks.sort(key=lambda item: item.score, reverse=True)
        return scored_tasks

    async def mark_dispatched(self, task_id: str, notes: str | None = None) -> TaskRecord:
        return await self._transition_task(task_id, "dispatched", notes)

    async def mark_awaiting_load(self, task_id: str, notes: str | None = None) -> TaskRecord:
        return await self._transition_task(task_id, "awaiting_load", notes)

    async def mark_in_transit(self, task_id: str, notes: str | None = None) -> TaskRecord:
        return await self._transition_task(task_id, "in_transit", notes)

    async def mark_awaiting_unload(self, task_id: str, notes: str | None = None) -> TaskRecord:
        return await self._transition_task(task_id, "awaiting_unload", notes)

    async def mark_completed(self, task_id: str, notes: str | None = None) -> TaskRecord:
        task = await self._transition_task(task_id, "completed", notes)
        self._last_completed_destination_id = task.destination_id
        self._publish_terminal_event(EventName.TASK_COMPLETED, task)
        return task

    async def mark_failed(self, task_id: str, notes: str | None = None) -> TaskRecord:
        task = await self._transition_task(task_id, "failed", notes)
        self._publish_terminal_event(EventName.TASK_FAILED, task)
        return task

    async def cancel_task(self, task_id: str, notes: str | None = None) -> TaskRecord:
        task = await self._transition_task(task_id, "cancelled", notes)
        self._publish_terminal_event(EventName.TASK_CANCELLED, task)
        return task

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            try:
                await self._database.initialize()
            except DatabaseError as exc:
                raise TaskQueueError(str(exc)) from exc
            self._initialized = True

    def _utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _validate_non_empty(self, field_name: str, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise TaskQueueError(f"{field_name} must not be empty")
        return value.strip()

    def _validate_transition(self, current: str, new: str) -> None:
        allowed = ALLOWED_TRANSITIONS.get(current, set())
        if new not in allowed:
            logger.warning("Invalid task transition", extra={"current": current, "new": new})
            raise InvalidTaskTransitionError(f"Invalid task transition: {current} -> {new}")

    def _build_summary(self, tasks: list[TaskRecord]) -> QueueSummary:
        counts = {status: 0 for status in ALL_TASK_STATUSES}
        for task in tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        return QueueSummary(
            total=len(tasks),
            queued=counts["queued"],
            dispatched=counts["dispatched"],
            awaiting_load=counts["awaiting_load"],
            in_transit=counts["in_transit"],
            awaiting_unload=counts["awaiting_unload"],
            completed=counts["completed"],
            failed=counts["failed"],
            cancelled=counts["cancelled"],
        )

    def _score_task(self, task: TaskRecord, robot_position: tuple[float, float] | None = None) -> ScoredTask:
        created_at = datetime.fromisoformat(task.created_at)
        age_seconds = max((self._utc_now() - created_at).total_seconds(), 0.0)
        priority_component = self._priority_weight * float(task.priority)
        recency_component = self._recency_weight * (1.0 / max(age_seconds, 1.0))
        proximity_component = self._proximity_weight * 0.0
        direction_bonus_component = (
            self._direction_bonus
            if self._last_completed_destination_id is not None
            and task.destination_id == self._last_completed_destination_id
            else 0.0
        )
        components = {
            "priority": priority_component,
            "recency": recency_component,
            "proximity": proximity_component,
            "direction_bonus": direction_bonus_component,
        }
        score = sum(components.values())
        logger.debug("Task scored", extra={"task_id": task.id, "score": score, **components})
        return ScoredTask(task=task, score=score, components=components)

    async def _transition_task(self, task_id: str, new_status: str, notes: str | None) -> TaskRecord:
        self._validate_non_empty("task_id", task_id)
        await self._ensure_initialized()
        async with self._lock:
            current_task = await self.get_task(task_id)
            self._validate_transition(current_task.status, new_status)
            try:
                updated_task = await self._database.update_task_status(task_id, new_status, notes=notes)
            except DatabaseError as exc:
                raise TaskQueueError(str(exc)) from exc

        self._publish_event(EventName.TASK_STATUS_CHANGED, self._event_payload(updated_task), task_id=updated_task.id)
        logger.info(
            "Task status updated",
            extra={"task_id": updated_task.id, "from_status": current_task.status, "to_status": new_status},
        )
        return updated_task

    def _event_payload(self, task: TaskRecord) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_id": task.id,
            "station_id": task.station_id,
            "destination_id": task.destination_id,
            "status": task.status,
            "priority": task.priority,
            "batch_id": task.batch_id,
        }
        if task.notes is not None:
            payload["notes"] = task.notes
        return payload

    def _publish_terminal_event(self, event_name: EventName, task: TaskRecord) -> None:
        self._publish_event(event_name, self._event_payload(task), task_id=task.id)

    def _publish_event(self, event_name: EventName, payload: dict[str, Any], *, task_id: str | None) -> None:
        try:
            get_event_bus().publish_nowait(event_name, payload=payload, source=__name__, task_id=task_id)
        except Exception:
            logger.debug("Task queue event publish skipped", extra={"event_name": event_name.value})


task_queue = TaskQueue()


def get_task_queue() -> TaskQueue:
    return task_queue


__all__ = [
    "InvalidTaskTransitionError",
    "QueueSummary",
    "ScoredTask",
    "TaskQueue",
    "TaskQueueError",
    "get_task_queue",
    "task_queue",
]
