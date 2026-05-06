from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from apps.patrol.observation.anomaly_decider import (
    AnomalyDecider,
    DecisionResult,
    DetectedObject,
    VisionAnalysisResult,
)
from apps.patrol.observation.anomaly_log import AnomalyLog, get_anomaly_log
from apps.patrol.observation.video_capture import VideoCapture, get_video_capture
from apps.patrol.observation.vision_analyser import VisionAnalyser, get_vision_analyser
from apps.patrol.observation.zone_config import ZoneConfig, ZoneConfigError, ZoneNotFoundError, get_zone_config
from shared.core.database import utc_now_iso
from apps.patrol import events as patrol_events
from shared.core.event_bus import EventName, get_event_bus
from shared.core.logger import get_logger


logger = get_logger(__name__)

ALLOWED_SEVERITIES = {"info", "warning", "critical"}


class ObserverError(Exception):
    """Raised when patrol observation summary data is invalid."""


def _validate_non_empty(field_name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ObserverError(f"{field_name} must not be empty")
    return value.strip()


@dataclass(frozen=True)
class ObservationSummary:
    waypoint_name: str
    zone_id: str
    observed_at: str
    frame_captured: bool
    analysis_source: str
    objects_detected: list[DetectedObject]
    alert_required: bool
    severity: str
    anomaly_id: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "waypoint_name", _validate_non_empty("waypoint_name", self.waypoint_name))
        object.__setattr__(self, "zone_id", _validate_non_empty("zone_id", self.zone_id))
        object.__setattr__(self, "observed_at", _validate_non_empty("observed_at", self.observed_at))
        if self.severity not in ALLOWED_SEVERITIES:
            allowed = ", ".join(sorted(ALLOWED_SEVERITIES))
            raise ObserverError(f"severity must be one of: {allowed}")
        if not isinstance(self.objects_detected, list) or any(not isinstance(item, DetectedObject) for item in self.objects_detected):
            raise ObserverError("objects_detected must be a list[DetectedObject]")
        object.__setattr__(self, "objects_detected", list(self.objects_detected))

    def to_dict(self) -> dict[str, Any]:
        return {
            "waypoint_name": self.waypoint_name,
            "zone_id": self.zone_id,
            "observed_at": self.observed_at,
            "frame_captured": self.frame_captured,
            "analysis_source": self.analysis_source,
            "objects_detected": [asdict(item) for item in self.objects_detected],
            "alert_required": self.alert_required,
            "severity": self.severity,
            "anomaly_id": self.anomaly_id,
            "error": self.error,
        }


class Observer:
    def __init__(
        self,
        zone_config: ZoneConfig | None = None,
        video_capture: VideoCapture | None = None,
        vision_analyser: VisionAnalyser | None = None,
        anomaly_decider: AnomalyDecider | None = None,
        anomaly_log: AnomalyLog | None = None,
    ) -> None:
        self._zone_config = zone_config or get_zone_config()
        self._video_capture = video_capture or get_video_capture()
        self._vision_analyser = vision_analyser or get_vision_analyser()
        self._anomaly_decider = anomaly_decider or AnomalyDecider()
        self._anomaly_log = anomaly_log or get_anomaly_log()

    async def observe(
        self,
        waypoint_name: str,
        zone_id: str,
        cycle_id: str,
        task_id: str | None = None,
    ) -> ObservationSummary:
        observed_at = utc_now_iso()
        normalized_waypoint_name = waypoint_name.strip() if isinstance(waypoint_name, str) and waypoint_name.strip() else "unknown"
        normalized_zone_id = zone_id.strip() if isinstance(zone_id, str) and zone_id.strip() else "unknown"
        normalized_cycle_id = cycle_id.strip() if isinstance(cycle_id, str) and cycle_id.strip() else "unknown"

        try:
            zone = await self._zone_config.require_zone(normalized_zone_id)
        except (ZoneNotFoundError, ZoneConfigError):
            return ObservationSummary(
                waypoint_name=normalized_waypoint_name,
                zone_id=normalized_zone_id,
                observed_at=observed_at,
                frame_captured=False,
                analysis_source="none",
                objects_detected=[],
                alert_required=False,
                severity="info",
                error="zone not configured",
            )
        except Exception as exc:
            return ObservationSummary(
                waypoint_name=normalized_waypoint_name,
                zone_id=normalized_zone_id,
                observed_at=observed_at,
                frame_captured=False,
                analysis_source="none",
                objects_detected=[],
                alert_required=False,
                severity="info",
                error=str(exc),
            )

        frame_captured = False
        analysis_source = "none"
        objects_detected: list[DetectedObject] = []
        alert_required = False
        severity = "info"
        anomaly_id: str | None = None
        error: str | None = None

        try:
            frame = await self._video_capture.capture(zone_id=normalized_zone_id)
            frame_captured = frame is not None
            analysis_result = await self._vision_analyser.analyse(frame, zone)
            analysis_source = analysis_result.analysis_source
            objects_detected = list(analysis_result.objects_detected)
            error = analysis_result.error

            decision = self._anomaly_decider.decide(analysis_result, zone)
            alert_required = decision.alert_required
            severity = decision.severity

            if decision.alert_required:
                record = await self._anomaly_log.record(
                    cycle_id=normalized_cycle_id,
                    zone_id=normalized_zone_id,
                    waypoint_name=normalized_waypoint_name,
                    decision_result=decision,
                    metadata={"task_id": task_id} if task_id is not None else {},
                )
                if record is not None:
                    anomaly_id = record.anomaly_id
        except Exception as exc:
            logger.exception(
                "Patrol observation stage failed",
                extra={"waypoint_name": normalized_waypoint_name, "zone_id": normalized_zone_id},
            )
            error = str(exc)

        summary = ObservationSummary(
            waypoint_name=normalized_waypoint_name,
            zone_id=normalized_zone_id,
            observed_at=observed_at,
            frame_captured=frame_captured,
            analysis_source=analysis_source,
            objects_detected=objects_detected,
            alert_required=alert_required,
            severity=severity,
            anomaly_id=anomaly_id,
            error=error,
        )

        payload = summary.to_dict()
        payload["cycle_id"] = normalized_cycle_id
        payload["task_id"] = task_id
        self._publish_event(
            patrol_events.PATROL_WAYPOINT_OBSERVED,
            payload,
            task_id=task_id,
        )

        if anomaly_id is not None:
            self._publish_event(
                patrol_events.PATROL_ANOMALY_DETECTED,
                {
                    "anomaly_id": anomaly_id,
                    "waypoint_name": normalized_waypoint_name,
                    "zone_id": normalized_zone_id,
                    "cycle_id": normalized_cycle_id,
                    "task_id": task_id,
                    "severity": severity,
                },
                task_id=task_id,
            )

        return summary

    @staticmethod
    def _publish_event(event_name: EventName | str, payload: dict[str, Any], task_id: str | None = None) -> None:
        try:
            get_event_bus().publish_nowait(event_name, payload, source="patrol.observer", task_id=task_id)
        except Exception:
            logger.debug("Patrol observer event publish skipped", extra={"event_name": event_name.value})


observer = Observer()


def get_observer() -> Observer:
    return observer


__all__ = [
    "ObservationSummary",
    "Observer",
    "ObserverError",
    "get_observer",
    "observer",
]
