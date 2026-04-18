from fastapi.testclient import TestClient

from autonomous_logistic.api.app import create_app
from autonomous_logistic.config.settings import AppSettings


def make_client(tmp_path):
    settings = AppSettings(db_path=str(tmp_path / "api.db"))
    return TestClient(create_app(settings))


def test_create_list_and_get_task_api(tmp_path):
    client = make_client(tmp_path)

    response = client.post(
        "/tasks",
        json={
            "source_point": "STATION_A",
            "destination_point": "STATION_B",
            "requested_by": "operator-1",
            "request_source": "remote_dispatch",
            "notes": "deliver parts",
        },
    )

    assert response.status_code == 201
    created = response.json()
    assert created["status"] == "CREATED"

    task_id = created["task_id"]
    assert client.get(f"/tasks/{task_id}").json()["task_id"] == task_id
    assert client.get("/tasks").json()[0]["task_id"] == task_id


def test_cancel_pause_resume_and_health_api(tmp_path):
    client = make_client(tmp_path)
    created = client.post(
        "/tasks",
        json={
            "source_point": "STATION_A",
            "destination_point": "STATION_B",
            "requested_by": "operator-1",
            "request_source": "remote_dispatch",
        },
    ).json()

    task_id = created["task_id"]
    paused = client.post(f"/tasks/{task_id}/pause").json()
    resumed = client.post(f"/tasks/{task_id}/resume").json()
    cancelled = client.post(f"/tasks/{task_id}/cancel").json()
    health = client.get("/health").json()

    assert paused["status"] == "PAUSED"
    assert resumed["status"] == "CREATED"
    assert cancelled["status"] == "CANCELLED"
    assert health["app_mode"] == "mock"
    assert health["robot"]["mode"] == "mock"


def test_stations_and_capabilities_api(tmp_path):
    client = make_client(tmp_path)

    stations = client.get("/stations").json()
    capabilities = client.get("/capabilities").json()

    assert stations[0]["station_id"] == "STATION_A"
    assert capabilities["has_remote_dispatch"] is True


def test_missing_task_returns_not_found_response(tmp_path):
    client = make_client(tmp_path)

    response = client.get("/tasks/missing-task")

    assert response.status_code == 404
    assert "Task not found" in response.json()["detail"]
