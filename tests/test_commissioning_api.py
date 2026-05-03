from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.logistics.commissioning.service import CommissioningStore, CurrentPose, PoseUnavailableError


TEST_OPERATOR_TOKEN = "test-operator-token"
TEST_SUPERVISOR_TOKEN = "test-supervisor-token"


def build_auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def commissioning_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from shared.core.config import AppConfig, AuthSection
    import shared.api.auth as auth_module
    import apps.logistics.api.commissioning as commissioning_module

    config = AppConfig(
        auth=AuthSection(
            operator_token=TEST_OPERATOR_TOKEN,
            qa_token="test-qa-token",
            supervisor_token=TEST_SUPERVISOR_TOKEN,
        )
    )
    store = _store(tmp_path)
    pose_state = {"pose": CurrentPose(x=3.0, y=4.0, yaw=1.2, source="slam_toolbox", confidence=0.9)}

    async def fake_pose_provider():
        pose = pose_state["pose"]
        if pose is None:
            raise PoseUnavailableError("Current pose unavailable")
        return pose

    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    monkeypatch.setattr(commissioning_module, "get_commissioning_store_dep", lambda: store)
    monkeypatch.setattr(commissioning_module, "get_current_pose_dep", fake_pose_provider)

    app = FastAPI()
    app.include_router(commissioning_module.create_commissioning_router())
    return TestClient(app), store, pose_state, tmp_path


def test_get_pose_returns_409_when_pose_unavailable(commissioning_client) -> None:
    client, _, pose_state, _ = commissioning_client
    pose_state["pose"] = None

    response = client.get("/commissioning/pose", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 409
    assert response.json()["detail"] == "Current pose unavailable"


def test_commissioning_requires_supervisor_auth(commissioning_client) -> None:
    client, *_ = commissioning_client

    missing = client.get("/commissioning/pose")
    operator = client.get("/commissioning/pose", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert missing.status_code == 401
    assert operator.status_code == 403


def test_get_pose_returns_current_pose(commissioning_client) -> None:
    client, *_ = commissioning_client

    response = client.get("/commissioning/pose", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["pose"]["x"] == 3.0
    assert body["pose"]["source"] == "slam_toolbox"


def test_mark_station_updates_temp_stations_file(commissioning_client) -> None:
    client, _, _, tmp_path = commissioning_client

    response = client.post(
        "/commissioning/stations/LINE_A/mark-current",
        json={"label": "Production Line A"},
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code == 200
    station = response.json()["station"]
    assert station["id"] == "LINE_A"
    assert station["name"] == "Production Line A"
    assert station["pose"]["x"] == 3.0
    on_disk = json.loads((tmp_path / "stations.json").read_text(encoding="utf-8"))
    assert on_disk["stations"][0]["pose"]["source"] == "slam_toolbox"


def test_add_waypoint_updates_temp_routes_file(commissioning_client) -> None:
    client, _, _, tmp_path = commissioning_client

    response = client.post(
        "/commissioning/routes/LINE_A_TO_QA/waypoints/add-current",
        json={"waypoint_id": "corridor_1", "hold": False, "hold_reason": None},
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code == 200
    route = response.json()["route"]
    assert route["id"] == "LINE_A_TO_QA"
    assert route["waypoint_count"] == 1
    on_disk = json.loads((tmp_path / "routes.json").read_text(encoding="utf-8"))
    assert on_disk["routes"][0]["waypoints"][0]["id"] == "corridor_1"


def test_placeholder_false_requires_waypoint(commissioning_client) -> None:
    client, *_ = commissioning_client

    response = client.post(
        "/commissioning/routes/LINE_A_TO_QA/placeholder",
        json={"placeholder": False},
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code == 409
    assert "waypoint" in response.json()["detail"]


def test_placeholder_false_accepted_after_waypoint(commissioning_client) -> None:
    client, *_ = commissioning_client
    client.post(
        "/commissioning/routes/LINE_A_TO_QA/waypoints/add-current",
        json={"waypoint_id": "wp_001"},
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    response = client.post(
        "/commissioning/routes/LINE_A_TO_QA/placeholder",
        json={"placeholder": False},
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["route"]["placeholder"] is False


def _store(tmp_path: Path) -> CommissioningStore:
    stations_path = tmp_path / "stations.json"
    routes_path = tmp_path / "routes.json"
    stations_path.write_text(
        json.dumps(
            {
                "stations": [
                    {
                        "id": "LINE_A",
                        "name": "Line A",
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
