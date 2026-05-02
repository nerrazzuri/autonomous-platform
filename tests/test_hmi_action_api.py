from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TEST_OPERATOR_TOKEN = "test-operator-token"
TEST_QA_TOKEN = "test-qa-token"
TEST_SUPERVISOR_TOKEN = "test-supervisor-token"


def build_auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_task_record(
    task_id: str,
    *,
    station_id: str = "A",
    destination_id: str = "QA",
    status: str = "queued",
):
    from core.database import TaskRecord

    return TaskRecord(
        id=task_id,
        station_id=station_id,
        destination_id=destination_id,
        batch_id=None,
        priority=0,
        status=status,
        created_at=datetime.now(timezone.utc).isoformat(),
        dispatched_at=None,
        completed_at=None,
        notes=None,
    )


class FakeTaskQueue:
    def __init__(self):
        self.tasks: dict[str, object] = {}

    async def get_task(self, task_id: str):
        from tasks.queue import TaskQueueError

        task = self.tasks.get(task_id)
        if task is None:
            raise TaskQueueError(f"Task not found: {task_id}")
        return task

    async def submit_task(self, **kwargs):
        task = make_task_record(
            "task-created",
            station_id=kwargs["station_id"],
            destination_id=kwargs["destination_id"],
        )
        self.tasks[task.id] = task
        return task


class FakeDispatcher:
    def __init__(self):
        self.pause_calls: list[str] = []
        self.resume_calls: int = 0

    async def pause(self, reason: str = "paused") -> None:
        self.pause_calls.append(reason)

    async def resume(self) -> None:
        self.resume_calls += 1


class FakeEventBus:
    def __init__(self):
        self.published: list[dict] = []
        self.publish_error: Exception | None = None

    async def publish(self, event_name, payload=None, *, source=None, task_id=None, correlation_id=None):
        if self.publish_error is not None:
            raise self.publish_error
        self.published.append({
            "event_name": event_name.value if hasattr(event_name, "value") else str(event_name),
            "payload": dict(payload or {}),
            "source": source,
            "task_id": task_id,
        })
        return self.published[-1]


@pytest.fixture
def hmi_client(monkeypatch: pytest.MonkeyPatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.config import AppConfig, AuthSection

    import api.auth as auth_module
    import api.hmi as hmi_module

    config = AppConfig(
        auth=AuthSection(
            operator_token=TEST_OPERATOR_TOKEN,
            qa_token=TEST_QA_TOKEN,
            supervisor_token=TEST_SUPERVISOR_TOKEN,
        )
    )
    queue = FakeTaskQueue()
    dispatcher = FakeDispatcher()
    event_bus = FakeEventBus()

    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    monkeypatch.setattr(hmi_module, "get_task_queue_dep", lambda: queue)
    monkeypatch.setattr(hmi_module, "get_dispatcher_dep", lambda: dispatcher)
    monkeypatch.setattr(hmi_module, "get_event_bus", lambda: event_bus)

    app = FastAPI()
    app.include_router(hmi_module.create_hmi_router())

    return TestClient(app), queue, dispatcher, event_bus


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_hmi_action_requires_auth(hmi_client) -> None:
    client, *_ = hmi_client

    response = client.post("/hmi/action", json={"action": "PAUSE_DISPATCHER"})

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------

def test_hmi_action_unknown_returns_400(hmi_client) -> None:
    client, *_ = hmi_client

    response = client.post(
        "/hmi/action",
        json={"action": "TELEPORT"},
        headers=build_auth_header(TEST_OPERATOR_TOKEN),
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# CONFIRM_LOAD
# ---------------------------------------------------------------------------

def test_hmi_confirm_load_success_publishes_event(hmi_client) -> None:
    client, queue, _, event_bus = hmi_client
    queue.tasks["t1"] = make_task_record("t1", station_id="A", destination_id="QA", status="awaiting_load")

    response = client.post(
        "/hmi/action",
        json={"action": "CONFIRM_LOAD", "task_id": "t1"},
        headers=build_auth_header(TEST_OPERATOR_TOKEN),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Load confirmed"
    assert body["display"]["page"] == "in_transit"
    assert len(event_bus.published) == 1
    assert event_bus.published[0]["event_name"] == "human.confirmed_load"
    assert event_bus.published[0]["task_id"] == "t1"


def test_hmi_confirm_load_wrong_status_returns_409(hmi_client) -> None:
    client, queue, *_ = hmi_client
    queue.tasks["t1"] = make_task_record("t1", status="queued")

    response = client.post(
        "/hmi/action",
        json={"action": "CONFIRM_LOAD", "task_id": "t1"},
        headers=build_auth_header(TEST_OPERATOR_TOKEN),
    )

    assert response.status_code == 409


def test_hmi_confirm_load_missing_task_id_returns_422(hmi_client) -> None:
    client, *_ = hmi_client

    response = client.post(
        "/hmi/action",
        json={"action": "CONFIRM_LOAD"},
        headers=build_auth_header(TEST_OPERATOR_TOKEN),
    )

    assert response.status_code == 422


def test_hmi_confirm_load_task_not_found_returns_404(hmi_client) -> None:
    client, *_ = hmi_client

    response = client.post(
        "/hmi/action",
        json={"action": "CONFIRM_LOAD", "task_id": "no-such-task"},
        headers=build_auth_header(TEST_OPERATOR_TOKEN),
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# CONFIRM_UNLOAD
# ---------------------------------------------------------------------------

def test_hmi_confirm_unload_success_publishes_event(hmi_client) -> None:
    client, queue, _, event_bus = hmi_client
    queue.tasks["t2"] = make_task_record("t2", station_id="A", destination_id="QA", status="awaiting_unload")

    response = client.post(
        "/hmi/action",
        json={"action": "CONFIRM_UNLOAD", "task_id": "t2"},
        headers=build_auth_header(TEST_OPERATOR_TOKEN),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Unload confirmed"
    assert body["display"]["page"] == "idle"
    assert len(event_bus.published) == 1
    assert event_bus.published[0]["event_name"] == "human.confirmed_unload"


def test_hmi_confirm_unload_wrong_status_returns_409(hmi_client) -> None:
    client, queue, *_ = hmi_client
    queue.tasks["t2"] = make_task_record("t2", status="in_transit")

    response = client.post(
        "/hmi/action",
        json={"action": "CONFIRM_UNLOAD", "task_id": "t2"},
        headers=build_auth_header(TEST_OPERATOR_TOKEN),
    )

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# PAUSE_DISPATCHER / RESUME_DISPATCHER
# ---------------------------------------------------------------------------

def test_hmi_pause_dispatcher(hmi_client) -> None:
    client, _, dispatcher, _ = hmi_client

    response = client.post(
        "/hmi/action",
        json={"action": "PAUSE_DISPATCHER"},
        headers=build_auth_header(TEST_OPERATOR_TOKEN),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["display"]["page"] == "paused"
    assert dispatcher.pause_calls == ["hmi"]


def test_hmi_resume_dispatcher(hmi_client) -> None:
    client, _, dispatcher, _ = hmi_client

    response = client.post(
        "/hmi/action",
        json={"action": "RESUME_DISPATCHER"},
        headers=build_auth_header(TEST_OPERATOR_TOKEN),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["display"]["page"] == "idle"
    assert dispatcher.resume_calls == 1


# ---------------------------------------------------------------------------
# CONFIRM_OBSTACLE_CLEARED
# ---------------------------------------------------------------------------

def test_hmi_confirm_obstacle_cleared_publishes_event(hmi_client) -> None:
    client, _, _, event_bus = hmi_client

    response = client.post(
        "/hmi/action",
        json={"action": "CONFIRM_OBSTACLE_CLEARED"},
        headers=build_auth_header(TEST_OPERATOR_TOKEN),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert len(event_bus.published) == 1
    assert event_bus.published[0]["event_name"] == "obstacle.cleared"


# ---------------------------------------------------------------------------
# REQUEST_TASK / RETURN_TO_DOCK
# ---------------------------------------------------------------------------

def test_hmi_request_task_creates_task(hmi_client) -> None:
    client, queue, *_ = hmi_client

    response = client.post(
        "/hmi/action",
        json={"action": "REQUEST_TASK", "station_id": "A", "destination_id": "QA"},
        headers=build_auth_header(TEST_OPERATOR_TOKEN),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "queued" in body["message"]
    assert "task-created" in queue.tasks


def test_hmi_request_task_missing_params_returns_422(hmi_client) -> None:
    client, *_ = hmi_client

    response = client.post(
        "/hmi/action",
        json={"action": "REQUEST_TASK", "station_id": "A"},
        headers=build_auth_header(TEST_OPERATOR_TOKEN),
    )

    assert response.status_code == 422


def test_hmi_return_to_dock_creates_task(hmi_client) -> None:
    client, queue, *_ = hmi_client

    response = client.post(
        "/hmi/action",
        json={"action": "RETURN_TO_DOCK", "station_id": "QA", "destination_id": "DOCK"},
        headers=build_auth_header(TEST_OPERATOR_TOKEN),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "task-created" in queue.tasks
