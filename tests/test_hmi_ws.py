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
HMI_IDENTITY = {"robot_id": "robot-1", "screen_id": "screen-front"}


def make_task_record(
    task_id: str,
    *,
    station_id: str = "LINE_A",
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
            "task-ws-created",
            station_id=kwargs["station_id"],
            destination_id=kwargs["destination_id"],
        )
        self.tasks[task.id] = task
        return task


class FakeRoute:
    def __init__(self, route_id: str):
        self.id = route_id


class FakeLogisticsRouteStore:
    def validate_task_request(self, origin_id: str, destination_id: str, *, allow_placeholder: bool = True):
        from apps.logistics.tasks.routes import RouteValidationError

        if (origin_id, destination_id) == ("LINE_A", "QA"):
            return FakeRoute("LINE_A_TO_QA")
        raise RouteValidationError("Route not configured")


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

    async def publish(self, event_name, payload=None, *, source=None, task_id=None, correlation_id=None):
        self.published.append({
            "event_name": event_name.value if hasattr(event_name, "value") else str(event_name),
            "payload": dict(payload or {}),
        })
        return self.published[-1]


@pytest.fixture
def hmi_ws_app(monkeypatch: pytest.MonkeyPatch):
    from fastapi import FastAPI

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
    route_store = FakeLogisticsRouteStore()

    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    monkeypatch.setattr(hmi_module, "get_task_queue_dep", lambda: queue)
    monkeypatch.setattr(hmi_module, "get_dispatcher_dep", lambda: dispatcher)
    monkeypatch.setattr(hmi_module, "get_event_bus", lambda: event_bus)
    monkeypatch.setattr(hmi_module, "get_logistics_route_store_dep", lambda: route_store)

    app = FastAPI()
    app.include_router(hmi_module.create_hmi_router())

    return app, queue, dispatcher, event_bus


# ---------------------------------------------------------------------------
# Connection auth
# ---------------------------------------------------------------------------

def test_ws_accepts_valid_operator_token(hmi_ws_app) -> None:
    from fastapi.testclient import TestClient

    app, *_ = hmi_ws_app
    with TestClient(app) as client:
        with client.websocket_connect(f"/hmi/ws?token={TEST_OPERATOR_TOKEN}"):
            pass


def test_ws_accepts_supervisor_token(hmi_ws_app) -> None:
    from fastapi.testclient import TestClient

    app, *_ = hmi_ws_app
    with TestClient(app) as client:
        with client.websocket_connect(f"/hmi/ws?token={TEST_SUPERVISOR_TOKEN}"):
            pass


def test_ws_rejects_missing_token(hmi_ws_app) -> None:
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    app, *_ = hmi_ws_app
    with TestClient(app) as client:
        with pytest.raises((WebSocketDisconnect, Exception)):
            with client.websocket_connect("/hmi/ws"):
                pass


def test_ws_rejects_invalid_token(hmi_ws_app) -> None:
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    app, *_ = hmi_ws_app
    with TestClient(app) as client:
        with pytest.raises((WebSocketDisconnect, Exception)):
            with client.websocket_connect("/hmi/ws?token=bad-token"):
                pass


# ---------------------------------------------------------------------------
# Valid action — REQUEST_TASK
# ---------------------------------------------------------------------------

def test_ws_request_task_returns_accepted_response(hmi_ws_app) -> None:
    from fastapi.testclient import TestClient

    app, queue, *_ = hmi_ws_app
    with TestClient(app) as client:
        with client.websocket_connect(f"/hmi/ws?token={TEST_OPERATOR_TOKEN}") as ws:
            ws.send_json({
                **HMI_IDENTITY,
                "action": "REQUEST_TASK",
                "station_id": "LINE_A",
                "destination_id": "QA",
            })
            msg = ws.receive_json()

    assert msg["type"] == "hmi.action_response"
    assert msg["success"] is True
    assert "queued" in msg["message"]
    assert msg["robot_id"] == "robot-1"
    assert msg["screen_id"] == "screen-front"
    assert msg["display"]["page"] == "queued"
    assert "task-ws-created" in queue.tasks


def test_ws_request_task_invalid_route_returns_display_error(hmi_ws_app) -> None:
    from fastapi.testclient import TestClient

    app, queue, *_ = hmi_ws_app
    with TestClient(app) as client:
        with client.websocket_connect(f"/hmi/ws?token={TEST_OPERATOR_TOKEN}") as ws:
            ws.send_json({
                **HMI_IDENTITY,
                "action": "REQUEST_TASK",
                "station_id": "LINE_A",
                "destination_id": "LINE_B",
            })
            msg = ws.receive_json()

    assert msg["type"] == "hmi.action_response"
    assert msg["success"] is False
    assert msg["message"] == "Invalid route"
    assert msg["display"]["text"] == "Invalid route"
    assert "task-ws-created" not in queue.tasks


# ---------------------------------------------------------------------------
# Unknown action — connection stays open
# ---------------------------------------------------------------------------

def test_ws_unknown_action_returns_error_keeps_connection(hmi_ws_app) -> None:
    from fastapi.testclient import TestClient

    app, *_ = hmi_ws_app
    with TestClient(app) as client:
        with client.websocket_connect(f"/hmi/ws?token={TEST_OPERATOR_TOKEN}") as ws:
            ws.send_json({**HMI_IDENTITY, "action": "TELEPORT"})
            err = ws.receive_json()
            assert err["type"] == "hmi.action_response"
            assert err["success"] is False
            assert "TELEPORT" in err["message"]

            # send a second valid message to confirm connection is still alive
            ws.send_json({**HMI_IDENTITY, "action": "PAUSE_DISPATCHER"})
            ok = ws.receive_json()
            assert ok["success"] is True


# ---------------------------------------------------------------------------
# Invalid JSON — connection stays open
# ---------------------------------------------------------------------------

def test_ws_invalid_json_returns_error_keeps_connection(hmi_ws_app) -> None:
    from fastapi.testclient import TestClient

    app, *_ = hmi_ws_app
    with TestClient(app) as client:
        with client.websocket_connect(f"/hmi/ws?token={TEST_OPERATOR_TOKEN}") as ws:
            ws.send_text("not-valid-json{{{")
            err = ws.receive_json()
            assert err["type"] == "hmi.action_response"
            assert err["success"] is False
            assert "JSON" in err["message"] or "json" in err["message"].lower()

            # connection still usable
            ws.send_json({**HMI_IDENTITY, "action": "RESUME_DISPATCHER"})
            ok = ws.receive_json()
            assert ok["success"] is True


# ---------------------------------------------------------------------------
# Missing required fields — connection stays open
# ---------------------------------------------------------------------------

def test_ws_missing_robot_id_returns_validation_error(hmi_ws_app) -> None:
    from fastapi.testclient import TestClient

    app, *_ = hmi_ws_app
    with TestClient(app) as client:
        with client.websocket_connect(f"/hmi/ws?token={TEST_OPERATOR_TOKEN}") as ws:
            ws.send_json({"action": "PAUSE_DISPATCHER", "screen_id": "screen-front"})
            err = ws.receive_json()
            assert err["type"] == "hmi.action_response"
            assert err["success"] is False

            # connection still usable
            ws.send_json({**HMI_IDENTITY, "action": "PAUSE_DISPATCHER"})
            ok = ws.receive_json()
            assert ok["success"] is True


# ---------------------------------------------------------------------------
# CONFIRM_LOAD via WebSocket — same behavior as REST
# ---------------------------------------------------------------------------

def test_ws_confirm_load_success(hmi_ws_app) -> None:
    from fastapi.testclient import TestClient

    app, queue, _, event_bus = hmi_ws_app
    queue.tasks["t1"] = make_task_record("t1", station_id="LINE_A", destination_id="QA", status="awaiting_load")

    with TestClient(app) as client:
        with client.websocket_connect(f"/hmi/ws?token={TEST_OPERATOR_TOKEN}") as ws:
            ws.send_json({**HMI_IDENTITY, "action": "CONFIRM_LOAD", "task_id": "t1"})
            msg = ws.receive_json()

    assert msg["success"] is True
    assert msg["message"] == "Load confirmed"
    assert msg["task_id"] == "t1"
    assert msg["display"]["page"] == "in_transit"
    assert len(event_bus.published) == 1
    assert event_bus.published[0]["event_name"] == "human.confirmed_load"


def test_ws_confirm_load_wrong_status_returns_error(hmi_ws_app) -> None:
    from fastapi.testclient import TestClient

    app, queue, *_ = hmi_ws_app
    queue.tasks["t1"] = make_task_record("t1", status="queued")

    with TestClient(app) as client:
        with client.websocket_connect(f"/hmi/ws?token={TEST_OPERATOR_TOKEN}") as ws:
            ws.send_json({**HMI_IDENTITY, "action": "CONFIRM_LOAD", "task_id": "t1"})
            msg = ws.receive_json()

    assert msg["success"] is False
    assert "awaiting_load" in msg["message"]


# ---------------------------------------------------------------------------
# No raw serial bytes in response
# ---------------------------------------------------------------------------

def test_ws_response_contains_no_serial_bytes(hmi_ws_app) -> None:
    from fastapi.testclient import TestClient
    import json as json_mod

    app, *_ = hmi_ws_app
    with TestClient(app) as client:
        with client.websocket_connect(f"/hmi/ws?token={TEST_OPERATOR_TOKEN}") as ws:
            ws.send_json({**HMI_IDENTITY, "action": "PAUSE_DISPATCHER"})
            msg = ws.receive_json()

    raw = json_mod.dumps(msg)
    assert "\xff\xff\xff" not in raw
    assert "FF FF FF" not in raw
    assert "serial" not in raw.lower()
    assert "ttyUSB" not in raw
