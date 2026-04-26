from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from apps.patrol.observation.anomaly_decider import DecisionResult, DetectedObject
from shared.core.config import get_config
from shared.core.database import Database, DatabaseError, get_database, utc_now_iso
from shared.core.logger import get_logger


logger = get_logger(__name__)

ALLOWED_SEVERITIES = {"info", "warning", "critical"}


class AnomalyLogError(Exception):
    """Raised when patrol anomaly persistence fails or inputs are invalid."""


class AnomalyNotFoundError(AnomalyLogError):
    """Raised when a patrol anomaly cannot be found."""


def _validate_non_empty(field_name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnomalyLogError(f"{field_name} must not be empty")
    return value.strip()


def _parse_json_string(field_name: str, value: str, expected_type: type) -> Any:
    if not isinstance(value, str):
        raise AnomalyLogError(f"{field_name} must be a JSON string")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise AnomalyLogError(f"{field_name} must be valid JSON") from exc
    if not isinstance(parsed, expected_type):
        expected_name = "list" if expected_type is list else "dict"
        raise AnomalyLogError(f"{field_name} must represent a JSON {expected_name}")
    return parsed


def _parse_iso_timestamp(field_name: str, value: str) -> datetime:
    normalized = _validate_non_empty(field_name, value)
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AnomalyLogError(f"{field_name} must be a valid ISO timestamp") from exc


@dataclass(frozen=True)
class AnomalyRecord:
    anomaly_id: str
    cycle_id: str
    zone_id: str
    waypoint_name: str
    detected_at: str
    severity: str
    threat_objects_json: str
    confidence_max: float
    resolved_at: str | None = None
    resolved_by: str | None = None
    metadata_json: str = "{}"

    def __post_init__(self) -> None:
        object.__setattr__(self, "anomaly_id", _validate_non_empty("anomaly_id", self.anomaly_id))
        object.__setattr__(self, "cycle_id", _validate_non_empty("cycle_id", self.cycle_id))
        object.__setattr__(self, "zone_id", _validate_non_empty("zone_id", self.zone_id))
        object.__setattr__(self, "waypoint_name", _validate_non_empty("waypoint_name", self.waypoint_name))
        object.__setattr__(self, "detected_at", _validate_non_empty("detected_at", self.detected_at))
        _parse_iso_timestamp("detected_at", self.detected_at)
        if self.severity not in ALLOWED_SEVERITIES:
            allowed = ", ".join(sorted(ALLOWED_SEVERITIES))
            raise AnomalyLogError(f"severity must be one of: {allowed}")
        if not isinstance(self.confidence_max, (int, float)) or not 0.0 <= float(self.confidence_max) <= 1.0:
            raise AnomalyLogError("confidence_max must be between 0.0 and 1.0")
        object.__setattr__(self, "confidence_max", float(self.confidence_max))
        _parse_json_string("threat_objects_json", self.threat_objects_json, list)
        _parse_json_string("metadata_json", self.metadata_json, dict)
        if self.resolved_at is not None:
            _parse_iso_timestamp("resolved_at", self.resolved_at)
        if self.resolved_by is not None:
            object.__setattr__(self, "resolved_by", _validate_non_empty("resolved_by", self.resolved_by))

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly_id": self.anomaly_id,
            "cycle_id": self.cycle_id,
            "zone_id": self.zone_id,
            "waypoint_name": self.waypoint_name,
            "detected_at": self.detected_at,
            "severity": self.severity,
            "threat_objects_json": self.threat_objects_json,
            "confidence_max": self.confidence_max,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
            "metadata_json": self.metadata_json,
        }

    def threat_objects(self) -> list[dict[str, Any]]:
        return list(_parse_json_string("threat_objects_json", self.threat_objects_json, list))

    def metadata(self) -> dict[str, Any]:
        return dict(_parse_json_string("metadata_json", self.metadata_json, dict))

    @classmethod
    def from_row(cls, row) -> AnomalyRecord:
        data = dict(row)
        return cls(
            anomaly_id=data["anomaly_id"],
            cycle_id=data["cycle_id"],
            zone_id=data["zone_id"],
            waypoint_name=data["waypoint_name"],
            detected_at=data["detected_at"],
            severity=data["severity"],
            threat_objects_json=data["threat_objects_json"],
            confidence_max=data["confidence_max"],
            resolved_at=data.get("resolved_at"),
            resolved_by=data.get("resolved_by"),
            metadata_json=data.get("metadata_json", "{}"),
        )


class AnomalyLog:
    def __init__(
        self,
        database: Database | None = None,
        cooldown_seconds: float | None = None,
    ) -> None:
        self._database = database or get_database()
        resolved_cooldown = (
            get_config().patrol.anomaly_cooldown_seconds if cooldown_seconds is None else cooldown_seconds
        )
        if not isinstance(resolved_cooldown, (int, float)) or float(resolved_cooldown) < 0:
            raise AnomalyLogError("cooldown_seconds must be >= 0")
        self._cooldown_seconds = float(resolved_cooldown)
        self._init_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
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
                    CREATE TABLE IF NOT EXISTS patrol_anomalies (
                        anomaly_id TEXT PRIMARY KEY,
                        cycle_id TEXT NOT NULL,
                        zone_id TEXT NOT NULL,
                        waypoint_name TEXT NOT NULL,
                        detected_at TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        threat_objects_json TEXT NOT NULL,
                        confidence_max REAL NOT NULL,
                        resolved_at TEXT,
                        resolved_by TEXT,
                        metadata_json TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_patrol_anomalies_zone_id
                    ON patrol_anomalies (zone_id);

                    CREATE INDEX IF NOT EXISTS idx_patrol_anomalies_detected_at
                    ON patrol_anomalies (detected_at);

                    CREATE INDEX IF NOT EXISTS idx_patrol_anomalies_resolved_at
                    ON patrol_anomalies (resolved_at);
                    """
                )
                await connection.commit()
            except DatabaseError as exc:
                logger.exception("Patrol anomaly log initialization failed")
                raise AnomalyLogError(str(exc)) from exc
            except Exception as exc:
                logger.exception("Patrol anomaly log initialization failed")
                raise AnomalyLogError(f"Failed to initialize anomaly log: {exc}") from exc

            self._initialized = True

    async def record(
        self,
        cycle_id: str,
        zone_id: str,
        waypoint_name: str,
        decision_result: DecisionResult,
        metadata: dict[str, Any] | None = None,
    ) -> AnomalyRecord | None:
        normalized_cycle_id = _validate_non_empty("cycle_id", cycle_id)
        normalized_zone_id = _validate_non_empty("zone_id", zone_id)
        normalized_waypoint_name = _validate_non_empty("waypoint_name", waypoint_name)
        if not isinstance(decision_result, DecisionResult):
            raise AnomalyLogError("decision_result must be a DecisionResult")
        if decision_result.zone_id != normalized_zone_id:
            raise AnomalyLogError("decision_result.zone_id must match zone_id")
        if metadata is not None and not isinstance(metadata, dict):
            raise AnomalyLogError("metadata must be a dict when provided")
        if decision_result.alert_required is False:
            return None

        await self.initialize()

        last_record = await self.get_last_for_zone(normalized_zone_id)
        current_timestamp = utc_now_iso()
        if last_record is not None and self._within_cooldown(last_record.detected_at, current_timestamp):
            logger.info(
                "Patrol anomaly suppressed by cooldown",
                extra={"zone_id": normalized_zone_id, "cooldown_seconds": self._cooldown_seconds},
            )
            return None

        threat_objects_payload = [self._detected_object_to_dict(item) for item in decision_result.threat_objects]
        record = AnomalyRecord(
            anomaly_id=str(uuid4()),
            cycle_id=normalized_cycle_id,
            zone_id=normalized_zone_id,
            waypoint_name=normalized_waypoint_name,
            detected_at=current_timestamp,
            severity=decision_result.severity,
            threat_objects_json=json.dumps(threat_objects_payload),
            confidence_max=max((item.confidence for item in decision_result.threat_objects), default=0.0),
            metadata_json=json.dumps(metadata or {}),
        )

        async with self._lock:
            connection = await self._get_connection()
            try:
                await connection.execute(
                    """
                    INSERT INTO patrol_anomalies (
                        anomaly_id, cycle_id, zone_id, waypoint_name, detected_at, severity,
                        threat_objects_json, confidence_max, resolved_at, resolved_by, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.anomaly_id,
                        record.cycle_id,
                        record.zone_id,
                        record.waypoint_name,
                        record.detected_at,
                        record.severity,
                        record.threat_objects_json,
                        record.confidence_max,
                        record.resolved_at,
                        record.resolved_by,
                        record.metadata_json,
                    ),
                )
                await connection.commit()
            except Exception as exc:
                logger.exception(
                    "Patrol anomaly record failed",
                    extra={"zone_id": normalized_zone_id, "waypoint_name": normalized_waypoint_name},
                )
                raise AnomalyLogError(f"Failed to record anomaly: {exc}") from exc

        logger.info(
            "Patrol anomaly recorded",
            extra={"anomaly_id": record.anomaly_id, "zone_id": record.zone_id, "severity": record.severity},
        )
        return record

    async def resolve(self, anomaly_id: str, resolved_by: str) -> AnomalyRecord:
        normalized_anomaly_id = _validate_non_empty("anomaly_id", anomaly_id)
        normalized_resolved_by = _validate_non_empty("resolved_by", resolved_by)
        await self.initialize()

        existing = await self.get(normalized_anomaly_id)
        resolved_at = utc_now_iso()

        async with self._lock:
            connection = await self._get_connection()
            try:
                cursor = await connection.execute(
                    """
                    UPDATE patrol_anomalies
                    SET resolved_at = ?, resolved_by = ?
                    WHERE anomaly_id = ?
                    """,
                    (resolved_at, normalized_resolved_by, normalized_anomaly_id),
                )
                await connection.commit()
            except Exception as exc:
                logger.exception("Patrol anomaly resolve failed", extra={"anomaly_id": normalized_anomaly_id})
                raise AnomalyLogError(f"Failed to resolve anomaly: {exc}") from exc

        if cursor.rowcount == 0:
            raise AnomalyNotFoundError(f"Anomaly not found: {normalized_anomaly_id}")

        updated = AnomalyRecord(
            anomaly_id=existing.anomaly_id,
            cycle_id=existing.cycle_id,
            zone_id=existing.zone_id,
            waypoint_name=existing.waypoint_name,
            detected_at=existing.detected_at,
            severity=existing.severity,
            threat_objects_json=existing.threat_objects_json,
            confidence_max=existing.confidence_max,
            resolved_at=resolved_at,
            resolved_by=normalized_resolved_by,
            metadata_json=existing.metadata_json,
        )
        logger.info("Patrol anomaly resolved", extra={"anomaly_id": updated.anomaly_id, "resolved_by": normalized_resolved_by})
        return updated

    async def get(self, anomaly_id: str) -> AnomalyRecord:
        normalized_anomaly_id = _validate_non_empty("anomaly_id", anomaly_id)
        await self.initialize()

        connection = await self._get_connection()
        try:
            cursor = await connection.execute(
                "SELECT * FROM patrol_anomalies WHERE anomaly_id = ?",
                (normalized_anomaly_id,),
            )
            row = await cursor.fetchone()
        except Exception as exc:
            logger.exception("Patrol anomaly fetch failed", extra={"anomaly_id": normalized_anomaly_id})
            raise AnomalyLogError(f"Failed to get anomaly: {exc}") from exc

        if row is None:
            raise AnomalyNotFoundError(f"Anomaly not found: {normalized_anomaly_id}")
        return AnomalyRecord.from_row(row)

    async def list_unresolved(self, zone_id: str | None = None) -> list[AnomalyRecord]:
        normalized_zone_id = _validate_non_empty("zone_id", zone_id) if zone_id is not None else None
        await self.initialize()

        connection = await self._get_connection()
        try:
            if normalized_zone_id is None:
                cursor = await connection.execute(
                    """
                    SELECT * FROM patrol_anomalies
                    WHERE resolved_at IS NULL
                    ORDER BY detected_at DESC
                    """
                )
            else:
                cursor = await connection.execute(
                    """
                    SELECT * FROM patrol_anomalies
                    WHERE resolved_at IS NULL AND zone_id = ?
                    ORDER BY detected_at DESC
                    """,
                    (normalized_zone_id,),
                )
            rows = await cursor.fetchall()
        except Exception as exc:
            logger.exception("Patrol unresolved anomaly query failed", extra={"zone_id": normalized_zone_id})
            raise AnomalyLogError(f"Failed to list unresolved anomalies: {exc}") from exc

        return [AnomalyRecord.from_row(row) for row in rows]

    async def get_last_for_zone(self, zone_id: str) -> AnomalyRecord | None:
        normalized_zone_id = _validate_non_empty("zone_id", zone_id)
        await self.initialize()

        connection = await self._get_connection()
        try:
            cursor = await connection.execute(
                """
                SELECT * FROM patrol_anomalies
                WHERE zone_id = ? AND resolved_at IS NULL
                ORDER BY detected_at DESC
                LIMIT 1
                """,
                (normalized_zone_id,),
            )
            row = await cursor.fetchone()
        except Exception as exc:
            logger.exception("Patrol latest anomaly query failed", extra={"zone_id": normalized_zone_id})
            raise AnomalyLogError(f"Failed to get last anomaly for zone: {exc}") from exc

        return AnomalyRecord.from_row(row) if row is not None else None

    async def _get_connection(self):
        try:
            return await self._database._ensure_connected()
        except DatabaseError as exc:
            logger.exception("Patrol anomaly database connection failed")
            raise AnomalyLogError(str(exc)) from exc

    def _within_cooldown(self, last_detected_at: str, current_timestamp: str) -> bool:
        if self._cooldown_seconds == 0:
            return False
        previous_dt = _parse_iso_timestamp("detected_at", last_detected_at)
        current_dt = _parse_iso_timestamp("detected_at", current_timestamp)
        return (current_dt - previous_dt).total_seconds() <= self._cooldown_seconds

    @staticmethod
    def _detected_object_to_dict(item: DetectedObject) -> dict[str, Any]:
        if not isinstance(item, DetectedObject):
            raise AnomalyLogError("threat_objects must contain DetectedObject instances")
        return asdict(item)


anomaly_log = AnomalyLog()


def get_anomaly_log() -> AnomalyLog:
    return anomaly_log


__all__ = [
    "AnomalyLog",
    "AnomalyLogError",
    "AnomalyNotFoundError",
    "AnomalyRecord",
    "anomaly_log",
    "get_anomaly_log",
]
