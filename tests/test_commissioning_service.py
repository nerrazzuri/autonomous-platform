from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.logistics.commissioning.service import (
    CommissioningError,
    CommissioningStore,
    CurrentPose,
)


def test_mark_station_writes_current_pose(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pose = CurrentPose(x=1.25, y=2.5, yaw=1.57, source="slam_toolbox", confidence=0.95)

    station = store.mark_station("LINE_A", pose)

    assert station["id"] == "LINE_A"
    assert station["x"] == 1.25
    assert station["y"] == 2.5
    assert station["yaw"] == 1.57
    assert station["pose"]["source"] == "slam_toolbox"
    assert station["pose"]["confidence"] == 0.95
    assert station["pose"]["captured_at"].endswith("+00:00")
    assert _read_json(tmp_path / "stations.json")["stations"][0]["pose"]["x"] == 1.25


def test_mark_station_rejects_unknown_station(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(CommissioningError, match="Station not found"):
        store.mark_station("MISSING", _pose())


def test_append_waypoint_writes_route_waypoint(tmp_path: Path) -> None:
    store = _store(tmp_path)

    route = store.append_waypoint(
        "LINE_A_TO_QA",
        _pose(),
        waypoint_id="corridor_1",
        hold=True,
        hold_reason="load_check",
    )

    waypoint = route["waypoints"][0]
    assert waypoint["id"] == "corridor_1"
    assert waypoint["name"] == "corridor_1"
    assert waypoint["x"] == 1.0
    assert waypoint["y"] == 2.0
    assert waypoint["yaw"] == 0.5
    assert waypoint["heading_deg"] == pytest.approx(28.6478897565)
    assert waypoint["hold"] is True
    assert waypoint["hold_reason"] == "load_check"
    assert waypoint["source"] == "odometry"
    assert _read_json(tmp_path / "routes.json")["routes"][0]["waypoints"][0]["id"] == "corridor_1"


def test_append_waypoint_rejects_unknown_route(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(CommissioningError, match="Route not found"):
        store.append_waypoint("MISSING", _pose())


def test_placeholder_false_rejected_without_waypoints(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(CommissioningError, match="at least one waypoint"):
        store.set_route_placeholder("LINE_A_TO_QA", False)


def test_placeholder_false_accepted_with_waypoint(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append_waypoint("LINE_A_TO_QA", _pose(), waypoint_id="wp_001")

    route = store.set_route_placeholder("LINE_A_TO_QA", False)

    assert route["placeholder"] is False
    assert _read_json(tmp_path / "routes.json")["routes"][0]["placeholder"] is False


def test_atomic_write_leaves_valid_json(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.mark_station("LINE_A", _pose())
    store.append_waypoint("LINE_A_TO_QA", _pose())

    assert _read_json(tmp_path / "stations.json")["stations"][0]["id"] == "LINE_A"
    assert _read_json(tmp_path / "routes.json")["routes"][0]["id"] == "LINE_A_TO_QA"


def _store(tmp_path: Path) -> CommissioningStore:
    stations_path = tmp_path / "stations.json"
    routes_path = tmp_path / "routes.json"
    stations_path.write_text(
        json.dumps(
            {
                "stations": [
                    {
                        "id": "LINE_A",
                        "name": "Production Line A",
                        "station_type": "production_line",
                        "x": 0.0,
                        "y": 0.0,
                        "metadata": {},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    routes_path.write_text(
        json.dumps(
            {
                "routes": [
                    {
                        "id": "LINE_A_TO_QA",
                        "name": "Line A to QA",
                        "origin_id": "LINE_A",
                        "destination_id": "QA",
                        "active": True,
                        "placeholder": True,
                        "waypoints": [],
                        "metadata": {},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return CommissioningStore(stations_path=stations_path, routes_path=routes_path)


def _pose() -> CurrentPose:
    return CurrentPose(x=1.0, y=2.0, yaw=0.5, source="odometry", confidence=None)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
