from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_detected_object(*, label: str = "unknown vehicle", confidence: float = 0.8):
    from apps.patrol.observation.anomaly_decider import DetectedObject

    return DetectedObject(
        label=label,
        threat_level="SUSPICIOUS",
        confidence=confidence,
        reason="Unexpected object in zone",
    )


def make_decision_result(
    *,
    alert_required: bool = True,
    severity: str = "warning",
    zone_id: str = "PLANTATION_NORTH",
    threat_objects=None,
):
    from apps.patrol.observation.anomaly_decider import DecisionResult

    return DecisionResult(
        alert_required=alert_required,
        severity=severity,
        threat_objects=list(threat_objects or [make_detected_object()]),
        zone_id=zone_id,
        reason="Suspicious object detected",
    )


@pytest_asyncio.fixture
async def anomaly_env(tmp_path: Path):
    from shared.core.database import Database

    from apps.patrol.observation.anomaly_log import AnomalyLog

    database = Database(tmp_path / "data" / "quadruped.db")
    log = AnomalyLog(database=database, cooldown_seconds=300.0)
    yield log, database
    await database.close()


@pytest.mark.asyncio
async def test_initialize_creates_table(anomaly_env) -> None:
    log, database = anomaly_env

    await log.initialize()

    cursor = await database._connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='patrol_anomalies'"
    )
    row = await cursor.fetchone()

    assert row[0] == "patrol_anomalies"


@pytest.mark.asyncio
async def test_record_creates_anomaly(anomaly_env) -> None:
    log, _database = anomaly_env

    record = await log.record(
        cycle_id="cycle-1",
        zone_id="PLANTATION_NORTH",
        waypoint_name="north_observation_1",
        decision_result=make_decision_result(
            severity="critical",
            threat_objects=[
                make_detected_object(label="wild boar", confidence=0.6),
                make_detected_object(label="fire", confidence=0.95),
            ],
        ),
        metadata={"source": "manual-test"},
    )

    assert record is not None
    assert record.cycle_id == "cycle-1"
    assert record.zone_id == "PLANTATION_NORTH"
    assert record.waypoint_name == "north_observation_1"
    assert record.severity == "critical"
    assert record.confidence_max == pytest.approx(0.95)
    assert record.threat_objects()[0]["label"] == "wild boar"
    assert record.metadata() == {"source": "manual-test"}


@pytest.mark.asyncio
async def test_record_returns_none_when_no_alert_required(anomaly_env) -> None:
    log, _database = anomaly_env

    record = await log.record(
        cycle_id="cycle-1",
        zone_id="PLANTATION_NORTH",
        waypoint_name="north_observation_1",
        decision_result=make_decision_result(alert_required=False, severity="info", threat_objects=[]),
    )

    assert record is None


@pytest.mark.asyncio
async def test_second_record_within_cooldown_returns_none(anomaly_env, monkeypatch: pytest.MonkeyPatch) -> None:
    log, _database = anomaly_env
    timestamps = iter(
        [
            "2026-04-26T00:00:00+00:00",
            "2026-04-26T00:04:59+00:00",
        ]
    )

    anomaly_log_module = importlib.import_module("apps.patrol.observation.anomaly_log")

    monkeypatch.setattr(anomaly_log_module, "utc_now_iso", lambda: next(timestamps))

    first = await log.record(
        cycle_id="cycle-1",
        zone_id="PLANTATION_NORTH",
        waypoint_name="north_observation_1",
        decision_result=make_decision_result(),
    )
    second = await log.record(
        cycle_id="cycle-2",
        zone_id="PLANTATION_NORTH",
        waypoint_name="north_observation_1",
        decision_result=make_decision_result(),
    )

    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_record_after_cooldown_creates_new_anomaly(anomaly_env, monkeypatch: pytest.MonkeyPatch) -> None:
    log, _database = anomaly_env
    timestamps = iter(
        [
            "2026-04-26T00:00:00+00:00",
            "2026-04-26T00:05:01+00:00",
        ]
    )

    anomaly_log_module = importlib.import_module("apps.patrol.observation.anomaly_log")

    monkeypatch.setattr(anomaly_log_module, "utc_now_iso", lambda: next(timestamps))

    first = await log.record(
        cycle_id="cycle-1",
        zone_id="PLANTATION_NORTH",
        waypoint_name="north_observation_1",
        decision_result=make_decision_result(),
    )
    second = await log.record(
        cycle_id="cycle-2",
        zone_id="PLANTATION_NORTH",
        waypoint_name="north_observation_1",
        decision_result=make_decision_result(),
    )

    assert first is not None
    assert second is not None
    assert second.anomaly_id != first.anomaly_id


@pytest.mark.asyncio
async def test_resolve_sets_resolved_fields(anomaly_env, monkeypatch: pytest.MonkeyPatch) -> None:
    log, _database = anomaly_env
    timestamps = iter(
        [
            "2026-04-26T00:00:00+00:00",
            "2026-04-26T00:10:00+00:00",
        ]
    )

    anomaly_log_module = importlib.import_module("apps.patrol.observation.anomaly_log")

    monkeypatch.setattr(anomaly_log_module, "utc_now_iso", lambda: next(timestamps))

    record = await log.record(
        cycle_id="cycle-1",
        zone_id="PLANTATION_NORTH",
        waypoint_name="north_observation_1",
        decision_result=make_decision_result(),
    )
    assert record is not None

    resolved = await log.resolve(record.anomaly_id, "supervisor")

    assert resolved.resolved_at == "2026-04-26T00:10:00+00:00"
    assert resolved.resolved_by == "supervisor"


@pytest.mark.asyncio
async def test_resolve_missing_raises(anomaly_env) -> None:
    log, _database = anomaly_env

    from apps.patrol.observation.anomaly_log import AnomalyNotFoundError

    with pytest.raises(AnomalyNotFoundError):
        await log.resolve("missing", "supervisor")


@pytest.mark.asyncio
async def test_list_unresolved_excludes_resolved(anomaly_env, monkeypatch: pytest.MonkeyPatch) -> None:
    log, _database = anomaly_env
    timestamps = iter(
        [
            "2026-04-26T00:00:00+00:00",
            "2026-04-26T00:10:00+00:00",
            "2026-04-26T00:20:00+00:00",
        ]
    )

    anomaly_log_module = importlib.import_module("apps.patrol.observation.anomaly_log")

    monkeypatch.setattr(anomaly_log_module, "utc_now_iso", lambda: next(timestamps))

    first = await log.record(
        cycle_id="cycle-1",
        zone_id="PLANTATION_NORTH",
        waypoint_name="north_observation_1",
        decision_result=make_decision_result(),
    )
    second = await log.record(
        cycle_id="cycle-2",
        zone_id="WAREHOUSE_PERIMETER",
        waypoint_name="warehouse_observation_1",
        decision_result=make_decision_result(zone_id="WAREHOUSE_PERIMETER"),
    )
    assert first is not None
    assert second is not None

    await log.resolve(first.anomaly_id, "supervisor")

    unresolved = await log.list_unresolved()

    assert [item.anomaly_id for item in unresolved] == [second.anomaly_id]


@pytest.mark.asyncio
async def test_list_unresolved_filters_zone(anomaly_env, monkeypatch: pytest.MonkeyPatch) -> None:
    log, _database = anomaly_env
    timestamps = iter(
        [
            "2026-04-26T00:00:00+00:00",
            "2026-04-26T00:10:00+00:00",
        ]
    )

    anomaly_log_module = importlib.import_module("apps.patrol.observation.anomaly_log")

    monkeypatch.setattr(anomaly_log_module, "utc_now_iso", lambda: next(timestamps))

    await log.record(
        cycle_id="cycle-1",
        zone_id="PLANTATION_NORTH",
        waypoint_name="north_observation_1",
        decision_result=make_decision_result(),
    )
    warehouse = await log.record(
        cycle_id="cycle-2",
        zone_id="WAREHOUSE_PERIMETER",
        waypoint_name="warehouse_observation_1",
        decision_result=make_decision_result(zone_id="WAREHOUSE_PERIMETER"),
    )

    unresolved = await log.list_unresolved(zone_id="WAREHOUSE_PERIMETER")

    assert warehouse is not None
    assert [item.zone_id for item in unresolved] == ["WAREHOUSE_PERIMETER"]


@pytest.mark.asyncio
async def test_get_last_for_zone_returns_latest_unresolved(anomaly_env, monkeypatch: pytest.MonkeyPatch) -> None:
    log, _database = anomaly_env
    timestamps = iter(
        [
            "2026-04-26T00:00:00+00:00",
            "2026-04-26T00:06:00+00:00",
        ]
    )

    anomaly_log_module = importlib.import_module("apps.patrol.observation.anomaly_log")

    monkeypatch.setattr(anomaly_log_module, "utc_now_iso", lambda: next(timestamps))

    first = await log.record(
        cycle_id="cycle-1",
        zone_id="PLANTATION_NORTH",
        waypoint_name="north_observation_1",
        decision_result=make_decision_result(),
    )
    second = await log.record(
        cycle_id="cycle-2",
        zone_id="PLANTATION_NORTH",
        waypoint_name="north_observation_1",
        decision_result=make_decision_result(severity="critical"),
    )

    last = await log.get_last_for_zone("PLANTATION_NORTH")

    assert first is not None
    assert second is not None
    assert last is not None
    assert last.anomaly_id == second.anomaly_id


def test_anomaly_record_validation() -> None:
    from apps.patrol.observation.anomaly_log import AnomalyLogError, AnomalyRecord

    with pytest.raises(AnomalyLogError, match="severity"):
        AnomalyRecord(
            anomaly_id="anom-1",
            cycle_id="cycle-1",
            zone_id="PLANTATION_NORTH",
            waypoint_name="north_observation_1",
            detected_at="2026-04-26T00:00:00+00:00",
            severity="bad",
            threat_objects_json="[]",
            confidence_max=0.5,
        )

    with pytest.raises(AnomalyLogError, match="threat_objects_json"):
        AnomalyRecord(
            anomaly_id="anom-1",
            cycle_id="cycle-1",
            zone_id="PLANTATION_NORTH",
            waypoint_name="north_observation_1",
            detected_at="2026-04-26T00:00:00+00:00",
            severity="warning",
            threat_objects_json="{}",
            confidence_max=0.5,
        )

    with pytest.raises(AnomalyLogError, match="metadata_json"):
        AnomalyRecord(
            anomaly_id="anom-1",
            cycle_id="cycle-1",
            zone_id="PLANTATION_NORTH",
            waypoint_name="north_observation_1",
            detected_at="2026-04-26T00:00:00+00:00",
            severity="warning",
            threat_objects_json="[]",
            confidence_max=0.5,
            metadata_json="[]",
        )


def test_global_get_anomaly_log_returns_log() -> None:
    from apps.patrol.observation.anomaly_log import AnomalyLog, anomaly_log, get_anomaly_log

    assert get_anomaly_log() is anomaly_log
    assert isinstance(anomaly_log, AnomalyLog)
