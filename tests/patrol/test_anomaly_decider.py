from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_zone(*, with_time_rule: bool = False):
    from apps.patrol.observation.zone_config import TimeRule, ZoneDefinition

    time_rules = [TimeRule(after="18:00", before="06:00")] if with_time_rule else []
    return ZoneDefinition(
        zone_id="PLANTATION_NORTH",
        description="North plantation",
        normal_objects=["palm trees"],
        suspicious_objects=["unknown vehicle"],
        threat_objects=["fire", "wild boar"],
        time_rules=time_rules,
    )


def make_object(
    *,
    label: str = "unknown vehicle",
    threat_level: str = "SUSPICIOUS",
    confidence: float = 0.8,
    reason: str = "Unexpected object",
):
    from apps.patrol.observation.anomaly_decider import DetectedObject

    return DetectedObject(
        label=label,
        threat_level=threat_level,
        confidence=confidence,
        reason=reason,
    )


def make_result(*objects):
    from apps.patrol.observation.anomaly_decider import VisionAnalysisResult

    return VisionAnalysisResult(
        zone_id="PLANTATION_NORTH",
        objects_detected=list(objects),
        analysis_source="stub",
    )


def test_detected_object_validation() -> None:
    from apps.patrol.observation.anomaly_decider import AnomalyDecisionError, DetectedObject

    with pytest.raises(AnomalyDecisionError, match="label"):
        DetectedObject(label="", threat_level="NORMAL", confidence=0.5, reason="clear")

    with pytest.raises(AnomalyDecisionError, match="threat_level"):
        DetectedObject(label="person", threat_level="BAD", confidence=0.5, reason="clear")

    with pytest.raises(AnomalyDecisionError, match="reason"):
        DetectedObject(label="person", threat_level="NORMAL", confidence=0.5, reason="")


def test_threat_object_returns_critical() -> None:
    from apps.patrol.observation.anomaly_decider import AnomalyDecider

    decider = AnomalyDecider()

    result = decider.decide(
        make_result(make_object(label="fire", threat_level="THREAT", reason="Visible flames")),
        make_zone(),
        current_time=datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc),
    )

    assert result.alert_required is True
    assert result.severity == "critical"
    assert result.escalated is False
    assert [item.label for item in result.threat_objects] == ["fire"]


def test_suspicious_outside_time_window_returns_warning() -> None:
    from apps.patrol.observation.anomaly_decider import AnomalyDecider

    decider = AnomalyDecider()

    result = decider.decide(
        make_result(make_object()),
        make_zone(with_time_rule=True),
        current_time=datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc),
    )

    assert result.alert_required is True
    assert result.severity == "warning"
    assert result.escalated is False
    assert [item.label for item in result.threat_objects] == ["unknown vehicle"]


def test_suspicious_inside_time_window_escalates_critical() -> None:
    from apps.patrol.observation.anomaly_decider import AnomalyDecider

    decider = AnomalyDecider()

    result = decider.decide(
        make_result(make_object()),
        make_zone(with_time_rule=True),
        current_time=datetime(2026, 4, 26, 22, 0, tzinfo=timezone.utc),
    )

    assert result.alert_required is True
    assert result.severity == "critical"
    assert result.escalated is True


def test_normal_only_returns_info() -> None:
    from apps.patrol.observation.anomaly_decider import AnomalyDecider

    decider = AnomalyDecider()

    result = decider.decide(
        make_result(make_object(label="palm trees", threat_level="NORMAL", reason="Expected foliage")),
        make_zone(),
    )

    assert result.alert_required is False
    assert result.severity == "info"
    assert result.threat_objects == []


def test_empty_detection_returns_info() -> None:
    from apps.patrol.observation.anomaly_decider import AnomalyDecider

    decider = AnomalyDecider()

    result = decider.decide(
        make_result(),
        make_zone(),
    )

    assert result.alert_required is False
    assert result.severity == "info"
    assert result.reason == "No anomaly detected"


def test_consecutive_suspicious_escalates() -> None:
    from apps.patrol.observation.anomaly_decider import AnomalyDecider, DecisionResult

    decider = AnomalyDecider()
    previous = DecisionResult(
        alert_required=True,
        severity="warning",
        threat_objects=[make_object()],
        zone_id="PLANTATION_NORTH",
        reason="Suspicious object detected",
    )

    result = decider.decide(
        make_result(make_object()),
        make_zone(),
        previous_result=previous,
    )

    assert result.alert_required is True
    assert result.severity == "critical"
    assert result.escalated is True


def test_invalid_confidence_rejected() -> None:
    from apps.patrol.observation.anomaly_decider import AnomalyDecisionError, DetectedObject

    with pytest.raises(AnomalyDecisionError, match="confidence"):
        DetectedObject(label="person", threat_level="NORMAL", confidence=1.5, reason="clear")


def test_decision_result_validation() -> None:
    from apps.patrol.observation.anomaly_decider import AnomalyDecisionError, DecisionResult

    with pytest.raises(AnomalyDecisionError, match="severity"):
        DecisionResult(
            alert_required=False,
            severity="bad",
            threat_objects=[],
            zone_id="PLANTATION_NORTH",
            reason="No anomaly detected",
        )

    with pytest.raises(AnomalyDecisionError, match="zone_id"):
        DecisionResult(
            alert_required=False,
            severity="info",
            threat_objects=[],
            zone_id="",
            reason="No anomaly detected",
        )
