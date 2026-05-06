from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.patrol import events as patrol_events


def make_zone():
    from apps.patrol.observation.zone_config import ZoneDefinition

    return ZoneDefinition(
        zone_id="PLANTATION_NORTH",
        description="North plantation",
        normal_objects=["trees"],
        suspicious_objects=["unknown vehicle"],
        threat_objects=["fire"],
    )


def make_object():
    from apps.patrol.observation.anomaly_decider import DetectedObject

    return DetectedObject(
        label="unknown vehicle",
        threat_level="SUSPICIOUS",
        confidence=0.8,
        reason="Unexpected object",
    )


def make_analysis_result():
    from apps.patrol.observation.anomaly_decider import VisionAnalysisResult

    return VisionAnalysisResult(
        zone_id="PLANTATION_NORTH",
        objects_detected=[make_object()],
        analysis_source="stub",
    )


def make_decision_result(*, alert_required: bool = False, severity: str = "info"):
    from apps.patrol.observation.anomaly_decider import DecisionResult

    return DecisionResult(
        alert_required=alert_required,
        severity=severity,
        threat_objects=[make_object()] if alert_required else [],
        zone_id="PLANTATION_NORTH",
        reason="decision",
    )


class StubZoneConfig:
    def __init__(self, zone=None, exc: Exception | None = None):
        self._zone = zone
        self._exc = exc

    async def require_zone(self, zone_id: str):
        if self._exc is not None:
            raise self._exc
        return self._zone


class StubVideoCapture:
    def __init__(self, frame=None, exc: Exception | None = None):
        self._frame = frame
        self._exc = exc

    async def capture(self, **_kwargs):
        if self._exc is not None:
            raise self._exc
        return self._frame


class StubVisionAnalyser:
    def __init__(self, result=None, exc: Exception | None = None):
        self._result = result
        self._exc = exc
        self.enabled = False

    async def analyse(self, frame, zone):
        if self._exc is not None:
            raise self._exc
        return self._result


class StubDecider:
    def __init__(self, result=None, exc: Exception | None = None):
        self._result = result
        self._exc = exc

    def decide(self, analysis_result, zone):
        if self._exc is not None:
            raise self._exc
        return self._result


class StubAnomalyLog:
    def __init__(self, record=None, exc: Exception | None = None):
        self._record = record
        self._exc = exc

    async def record(self, **_kwargs):
        if self._exc is not None:
            raise self._exc
        return self._record


class StubAnomalyRecord:
    def __init__(self, anomaly_id: str):
        self.anomaly_id = anomaly_id


@pytest_asyncio.fixture
async def event_bus_env(monkeypatch: pytest.MonkeyPatch):
    from shared.core.event_bus import EventBus

    observer_module = importlib.import_module("apps.patrol.observation.observer")

    event_bus = EventBus()
    await event_bus.start()
    monkeypatch.setattr(observer_module, "get_event_bus", lambda: event_bus)
    yield event_bus, observer_module
    await event_bus.stop()


@pytest.mark.asyncio
async def test_observe_zone_not_found_returns_error_summary(event_bus_env) -> None:
    _event_bus, observer_module = event_bus_env
    from apps.patrol.observation.zone_config import ZoneNotFoundError

    observer = observer_module.Observer(
        zone_config=StubZoneConfig(exc=ZoneNotFoundError("missing")),
        video_capture=StubVideoCapture(),
        vision_analyser=StubVisionAnalyser(result=make_analysis_result()),
        anomaly_decider=StubDecider(result=make_decision_result()),
        anomaly_log=StubAnomalyLog(),
    )

    summary = await observer.observe("north_observation_1", "PLANTATION_NORTH", "cycle-1")

    assert summary.alert_required is False
    assert summary.severity == "info"
    assert summary.analysis_source == "none"
    assert summary.error == "zone not configured"


@pytest.mark.asyncio
async def test_observe_stub_pipeline_publishes_waypoint_observed(event_bus_env) -> None:
    event_bus, observer_module = event_bus_env
    events = []

    async def callback(event):
        events.append(event)

    event_bus.subscribe(patrol_events.PATROL_WAYPOINT_OBSERVED, callback, subscriber_name="test")

    observer = observer_module.Observer(
        zone_config=StubZoneConfig(zone=make_zone()),
        video_capture=StubVideoCapture(frame=None),
        vision_analyser=StubVisionAnalyser(
            result=observer_module.VisionAnalysisResult(
                zone_id="PLANTATION_NORTH",
                objects_detected=[],
                analysis_source="stub",
            )
        ),
        anomaly_decider=StubDecider(result=make_decision_result()),
        anomaly_log=StubAnomalyLog(),
    )

    summary = await observer.observe("north_observation_1", "PLANTATION_NORTH", "cycle-1", task_id="task-1")
    await event_bus.wait_until_idle()

    assert summary.frame_captured is False
    assert summary.analysis_source == "stub"
    assert len(events) == 1
    assert events[0].payload["waypoint_name"] == "north_observation_1"
    assert events[0].payload["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_observe_records_and_publishes_anomaly_when_alert_required(event_bus_env) -> None:
    event_bus, observer_module = event_bus_env
    anomaly_events = []

    async def callback(event):
        anomaly_events.append(event)

    event_bus.subscribe(patrol_events.PATROL_ANOMALY_DETECTED, callback, subscriber_name="test")

    observer = observer_module.Observer(
        zone_config=StubZoneConfig(zone=make_zone()),
        video_capture=StubVideoCapture(frame=None),
        vision_analyser=StubVisionAnalyser(result=make_analysis_result()),
        anomaly_decider=StubDecider(result=make_decision_result(alert_required=True, severity="warning")),
        anomaly_log=StubAnomalyLog(record=StubAnomalyRecord("anom-1")),
    )

    summary = await observer.observe("north_observation_1", "PLANTATION_NORTH", "cycle-1")
    await event_bus.wait_until_idle()

    assert summary.anomaly_id == "anom-1"
    assert summary.alert_required is True
    assert len(anomaly_events) == 1
    assert anomaly_events[0].payload["anomaly_id"] == "anom-1"


@pytest.mark.asyncio
async def test_stage_exception_captured_in_summary_error(event_bus_env) -> None:
    _event_bus, observer_module = event_bus_env

    observer = observer_module.Observer(
        zone_config=StubZoneConfig(zone=make_zone()),
        video_capture=StubVideoCapture(exc=RuntimeError("camera boom")),
        vision_analyser=StubVisionAnalyser(result=make_analysis_result()),
        anomaly_decider=StubDecider(result=make_decision_result()),
        anomaly_log=StubAnomalyLog(),
    )

    summary = await observer.observe("north_observation_1", "PLANTATION_NORTH", "cycle-1")

    assert summary.alert_required is False
    assert summary.severity == "info"
    assert "camera boom" in (summary.error or "")


def test_observation_summary_to_dict() -> None:
    from apps.patrol.observation.observer import ObservationSummary

    summary = ObservationSummary(
        waypoint_name="north_observation_1",
        zone_id="PLANTATION_NORTH",
        observed_at="2026-04-26T00:00:00+00:00",
        frame_captured=False,
        analysis_source="stub",
        objects_detected=[make_object()],
        alert_required=False,
        severity="info",
    )

    payload = summary.to_dict()

    assert payload["waypoint_name"] == "north_observation_1"
    assert payload["objects_detected"][0]["label"] == "unknown vehicle"


def test_global_get_observer_returns_observer() -> None:
    from apps.patrol.observation.observer import Observer, get_observer, observer

    assert get_observer() is observer
    assert isinstance(observer, Observer)
