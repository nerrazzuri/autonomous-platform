from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from apps.patrol.observation.zone_config import ZoneDefinition


ALLOWED_THREAT_LEVELS = {"NORMAL", "SUSPICIOUS", "THREAT"}
ALLOWED_SEVERITIES = {"info", "warning", "critical"}


class AnomalyDecisionError(Exception):
    """Raised when patrol anomaly analysis or decision data is invalid."""


def _validate_non_empty(field_name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnomalyDecisionError(f"{field_name} must not be empty")
    return value.strip()


@dataclass(frozen=True)
class DetectedObject:
    label: str
    threat_level: str
    confidence: float
    reason: str
    location_hint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _validate_non_empty("label", self.label))
        object.__setattr__(self, "reason", _validate_non_empty("reason", self.reason))
        if self.threat_level not in ALLOWED_THREAT_LEVELS:
            allowed = ", ".join(sorted(ALLOWED_THREAT_LEVELS))
            raise AnomalyDecisionError(f"threat_level must be one of: {allowed}")
        if not isinstance(self.confidence, (int, float)) or not 0.0 <= float(self.confidence) <= 1.0:
            raise AnomalyDecisionError("confidence must be between 0.0 and 1.0")
        object.__setattr__(self, "confidence", float(self.confidence))
        if self.location_hint is not None and (not isinstance(self.location_hint, str) or not self.location_hint.strip()):
            raise AnomalyDecisionError("location_hint must be a non-empty string when provided")


@dataclass(frozen=True)
class VisionAnalysisResult:
    zone_id: str
    objects_detected: list[DetectedObject]
    analysis_source: str
    raw_response: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "zone_id", _validate_non_empty("zone_id", self.zone_id))
        object.__setattr__(self, "analysis_source", _validate_non_empty("analysis_source", self.analysis_source))
        if not isinstance(self.objects_detected, list) or any(not isinstance(item, DetectedObject) for item in self.objects_detected):
            raise AnomalyDecisionError("objects_detected must be a list[DetectedObject]")
        object.__setattr__(self, "objects_detected", list(self.objects_detected))
        if self.raw_response is not None and not isinstance(self.raw_response, str):
            raise AnomalyDecisionError("raw_response must be a string when provided")
        if self.error is not None and not isinstance(self.error, str):
            raise AnomalyDecisionError("error must be a string when provided")


@dataclass(frozen=True)
class DecisionResult:
    alert_required: bool
    severity: str
    threat_objects: list[DetectedObject]
    zone_id: str
    reason: str
    escalated: bool = False

    def __post_init__(self) -> None:
        if self.severity not in ALLOWED_SEVERITIES:
            allowed = ", ".join(sorted(ALLOWED_SEVERITIES))
            raise AnomalyDecisionError(f"severity must be one of: {allowed}")
        object.__setattr__(self, "zone_id", _validate_non_empty("zone_id", self.zone_id))
        object.__setattr__(self, "reason", _validate_non_empty("reason", self.reason))
        if not isinstance(self.threat_objects, list) or any(not isinstance(item, DetectedObject) for item in self.threat_objects):
            raise AnomalyDecisionError("threat_objects must be a list[DetectedObject]")
        object.__setattr__(self, "threat_objects", list(self.threat_objects))


class AnomalyDecider:
    def decide(
        self,
        analysis_result: VisionAnalysisResult,
        zone: ZoneDefinition,
        current_time: datetime | None = None,
        previous_result: DecisionResult | None = None,
    ) -> DecisionResult:
        if not isinstance(analysis_result, VisionAnalysisResult):
            raise AnomalyDecisionError("analysis_result must be a VisionAnalysisResult")
        if not isinstance(zone, ZoneDefinition):
            raise AnomalyDecisionError("zone must be a ZoneDefinition")
        if analysis_result.zone_id != zone.zone_id:
            raise AnomalyDecisionError("analysis_result.zone_id must match zone.zone_id")

        now = current_time if current_time is not None else datetime.now(timezone.utc)

        if not analysis_result.objects_detected:
            return DecisionResult(
                alert_required=False,
                severity="info",
                threat_objects=[],
                zone_id=zone.zone_id,
                reason="No anomaly detected",
            )

        direct_threats = [item for item in analysis_result.objects_detected if item.threat_level == "THREAT"]
        suspicious_objects = [item for item in analysis_result.objects_detected if item.threat_level == "SUSPICIOUS"]

        if direct_threats:
            labels = ", ".join(item.label for item in direct_threats)
            return DecisionResult(
                alert_required=True,
                severity="critical",
                threat_objects=direct_threats,
                zone_id=zone.zone_id,
                reason=f"Threat objects detected: {labels}",
            )

        if suspicious_objects:
            labels = ", ".join(item.label for item in suspicious_objects)
            if any(rule.matches(now) and rule.escalate_suspicious_to == "THREAT" for rule in zone.time_rules):
                return DecisionResult(
                    alert_required=True,
                    severity="critical",
                    threat_objects=suspicious_objects,
                    zone_id=zone.zone_id,
                    reason=f"Suspicious objects escalated by time rule: {labels}",
                    escalated=True,
                )

            if (
                previous_result is not None
                and previous_result.zone_id == zone.zone_id
                and previous_result.alert_required is True
                and previous_result.severity == "warning"
            ):
                return DecisionResult(
                    alert_required=True,
                    severity="critical",
                    threat_objects=suspicious_objects,
                    zone_id=zone.zone_id,
                    reason=f"Suspicious objects escalated after consecutive observations: {labels}",
                    escalated=True,
                )

            return DecisionResult(
                alert_required=True,
                severity="warning",
                threat_objects=suspicious_objects,
                zone_id=zone.zone_id,
                reason=f"Suspicious objects detected: {labels}",
            )

        return DecisionResult(
            alert_required=False,
            severity="info",
            threat_objects=[],
            zone_id=zone.zone_id,
            reason="Only normal objects detected",
        )


__all__ = [
    "AnomalyDecider",
    "AnomalyDecisionError",
    "DecisionResult",
    "DetectedObject",
    "VisionAnalysisResult",
]
