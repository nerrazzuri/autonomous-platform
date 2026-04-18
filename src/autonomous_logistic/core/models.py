from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    DISPATCHED = "DISPATCHED"
    MOVING_TO_SOURCE = "MOVING_TO_SOURCE"
    ARRIVED_SOURCE = "ARRIVED_SOURCE"
    LOADING = "LOADING"
    MOVING_TO_DESTINATION = "MOVING_TO_DESTINATION"
    ARRIVED_DESTINATION = "ARRIVED_DESTINATION"
    UNLOADING = "UNLOADING"
    RETURNING = "RETURNING"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class AuditEventType(str, Enum):
    TASK_CREATED = "task_created"
    TASK_ASSIGNED = "task_assigned"
    TASK_STARTED = "task_started"
    STATE_CHANGED = "state_changed"
    NAVIGATION_REQUESTED = "navigation_requested"
    OBSTACLE_EVENT = "obstacle_event"
    OPERATOR_INTERACTION = "operator_interaction"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"
    TASK_PAUSED = "task_paused"
    TASK_RESUMED = "task_resumed"
    EMERGENCY_STOP = "emergency_stop"


@dataclass(frozen=True)
class Capabilities:
    has_lidar: bool = False
    has_speaker: bool = False
    has_screen: bool = False
    has_touch_input: bool = False
    has_button_panel: bool = False
    has_remote_dispatch: bool = True
    has_local_hmi: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "has_lidar": self.has_lidar,
            "has_speaker": self.has_speaker,
            "has_screen": self.has_screen,
            "has_touch_input": self.has_touch_input,
            "has_button_panel": self.has_button_panel,
            "has_remote_dispatch": self.has_remote_dispatch,
            "has_local_hmi": self.has_local_hmi,
        }


@dataclass
class Station:
    station_id: str
    name: str
    position: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransportTask:
    task_id: str
    source_point: str
    destination_point: str
    requested_by: str
    request_source: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    status: TaskStatus = TaskStatus.CREATED
    error_code: str | None = None
    notes: str | None = None
    previous_status: TaskStatus | None = None

    @classmethod
    def create(
        cls,
        source_point: str,
        destination_point: str,
        requested_by: str,
        request_source: str,
        notes: str | None = None,
    ) -> "TransportTask":
        return cls(
            task_id=str(uuid4()),
            source_point=source_point,
            destination_point=destination_point,
            requested_by=requested_by,
            request_source=request_source,
            created_at=utc_now(),
            notes=notes,
        )


@dataclass
class AuditEvent:
    event_id: str
    event_type: AuditEventType
    created_at: datetime
    task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        event_type: AuditEventType,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "AuditEvent":
        return cls(
            event_id=str(uuid4()),
            event_type=event_type,
            task_id=task_id,
            created_at=utc_now(),
            metadata=metadata or {},
        )
