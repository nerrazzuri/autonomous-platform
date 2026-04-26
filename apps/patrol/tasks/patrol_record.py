from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


ALLOWED_TRIGGER_SOURCES = {"schedule", "manual", "alert"}


class PatrolTaskError(Exception):
    """Raised when patrol cycle data is invalid."""


class InvalidCycleTransition(PatrolTaskError):
    """Raised when a patrol cycle transition is not allowed."""


class PatrolCycleStatus(str, Enum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    SUSPENDED = "suspended"


@dataclass(frozen=True)
class PatrolRecord:
    cycle_id: str
    route_id: str
    status: PatrolCycleStatus
    triggered_by: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    waypoints_total: int = 0
    waypoints_observed: int = 0
    anomaly_ids: list[str] = field(default_factory=list)
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "cycle_id", self._validate_non_empty("cycle_id", self.cycle_id))
        object.__setattr__(self, "route_id", self._validate_non_empty("route_id", self.route_id))
        object.__setattr__(self, "created_at", self._validate_non_empty("created_at", self.created_at))
        object.__setattr__(self, "triggered_by", self._validate_triggered_by(self.triggered_by))
        object.__setattr__(self, "status", self._normalize_status(self.status))
        object.__setattr__(self, "waypoints_total", self._validate_non_negative("waypoints_total", self.waypoints_total))
        object.__setattr__(
            self,
            "waypoints_observed",
            self._validate_non_negative("waypoints_observed", self.waypoints_observed),
        )
        object.__setattr__(self, "anomaly_ids", self._validate_anomaly_ids(self.anomaly_ids))
        if self.waypoints_total != 0 and self.waypoints_observed > self.waypoints_total:
            raise PatrolTaskError("waypoints_observed must be less than or equal to waypoints_total")

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "route_id": self.route_id,
            "status": self.status.value,
            "triggered_by": self.triggered_by,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "waypoints_total": self.waypoints_total,
            "waypoints_observed": self.waypoints_observed,
            "anomaly_ids": list(self.anomaly_ids),
            "failure_reason": self.failure_reason,
        }

    @classmethod
    def from_row(cls, row) -> PatrolRecord:
        data = dict(row)
        if "anomaly_ids_json" in data and "anomaly_ids" not in data:
            data["anomaly_ids"] = json.loads(data["anomaly_ids_json"])
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PatrolRecord:
        return cls(
            cycle_id=data["cycle_id"],
            route_id=data["route_id"],
            status=data["status"],
            triggered_by=data["triggered_by"],
            created_at=data["created_at"],
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            waypoints_total=data.get("waypoints_total", 0),
            waypoints_observed=data.get("waypoints_observed", 0),
            anomaly_ids=data.get("anomaly_ids", []),
            failure_reason=data.get("failure_reason"),
        )

    @staticmethod
    def _validate_non_empty(field_name: str, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise PatrolTaskError(f"{field_name} must not be empty")
        return value.strip()

    @staticmethod
    def _validate_non_negative(field_name: str, value: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise PatrolTaskError(f"{field_name} must be a non-negative integer")
        return value

    @staticmethod
    def _validate_triggered_by(value: str) -> str:
        if not isinstance(value, str) or value not in ALLOWED_TRIGGER_SOURCES:
            raise PatrolTaskError("triggered_by must be one of: schedule, manual, alert")
        return value

    @staticmethod
    def _validate_anomaly_ids(value: list[str]) -> list[str]:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise PatrolTaskError("anomaly_ids must be a list[str]")
        return list(value)

    @staticmethod
    def _normalize_status(value: PatrolCycleStatus | str) -> PatrolCycleStatus:
        if isinstance(value, PatrolCycleStatus):
            return value
        try:
            return PatrolCycleStatus(value)
        except ValueError as exc:
            raise PatrolTaskError(f"status must be one of: {[status.value for status in PatrolCycleStatus]}") from exc


class PatrolCycleStateMachine:
    _ALLOWED_TRANSITIONS: dict[PatrolCycleStatus, set[PatrolCycleStatus]] = {
        PatrolCycleStatus.SCHEDULED: {PatrolCycleStatus.ACTIVE},
        PatrolCycleStatus.ACTIVE: {
            PatrolCycleStatus.COMPLETED,
            PatrolCycleStatus.FAILED,
            PatrolCycleStatus.SUSPENDED,
        },
        PatrolCycleStatus.SUSPENDED: {
            PatrolCycleStatus.ACTIVE,
            PatrolCycleStatus.FAILED,
        },
        PatrolCycleStatus.COMPLETED: set(),
        PatrolCycleStatus.FAILED: set(),
    }

    @classmethod
    def can_transition(
        cls,
        current: PatrolCycleStatus | str,
        new: PatrolCycleStatus | str,
    ) -> bool:
        current_status = PatrolRecord._normalize_status(current)
        new_status = PatrolRecord._normalize_status(new)
        return new_status in cls._ALLOWED_TRANSITIONS.get(current_status, set())

    @classmethod
    def transition_status(
        cls,
        current: PatrolCycleStatus | str,
        new: PatrolCycleStatus | str,
    ) -> PatrolCycleStatus:
        current_status = PatrolRecord._normalize_status(current)
        new_status = PatrolRecord._normalize_status(new)
        if not cls.can_transition(current_status, new_status):
            raise InvalidCycleTransition(f"Invalid patrol cycle transition: {current_status.value} -> {new_status.value}")
        return new_status


__all__ = [
    "InvalidCycleTransition",
    "PatrolCycleStateMachine",
    "PatrolCycleStatus",
    "PatrolRecord",
    "PatrolTaskError",
]
