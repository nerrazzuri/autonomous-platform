from __future__ import annotations

import sys
from dataclasses import replace
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
    priority: int = 0,
    status: str = "queued",
    notes: str | None = None,
):
    from core.database import TaskRecord

    return TaskRecord(
        id=task_id,
        station_id=station_id,
        destination_id=destination_id,
        batch_id=None,
        priority=priority,
        status=status,
        created_at=datetime.now(timezone.utc).isoformat(),
        dispatched_at=None,
        completed_at=None,
        notes=notes,
    )


def make_state():
    from quadruped.sdk_adapter import QuadrupedMode
    from quadruped.state_monitor import QuadrupedState

    return QuadrupedState(
        timestamp=datetime.now(timezone.utc),
        battery_pct=88,
        position=(1.0, 2.0, 0.0),
        rpy=(0.0, 0.0, 0.1),
        control_mode=3,
        connection_ok=True,
        mode=QuadrupedMode.STANDING,
    )


def make_route(route_id: str = "A_TO_QA"):
    from navigation.route_store import RouteDefinition, Waypoint

    return RouteDefinition(
        id=route_id,
        name="Line A to QA",
        origin_id="A",
        destination_id="QA",
        active=True,
        metadata={"notes": "sample"},
        waypoints=[
            Waypoint(name="wp-1", x=0.0, y=0.0, heading_deg=0.0, velocity=0.25, hold=True),
            Waypoint(name="wp-2", x=5.0, y=2.0, heading_deg=90.0, velocity=0.2, hold=False),
        ],
    )


class FakeTaskQueue:
    def __init__(self):
        from tasks.queue import QueueSummary

        self.tasks: dict[str, object] = {}
        self.summary = QueueSummary(
            total=0,
            queued=0,
            dispatched=0,
            awaiting_load=0,
            in_transit=0,
            awaiting_unload=0,
            completed=0,
            failed=0,
            cancelled=0,
        )
        self.cancel_error: Exception | None = None

    async def submit_task(self, **kwargs):
        task = make_task_record(
            kwargs.get("task_id", "task-created"),
            station_id=kwargs["station_id"],
            destination_id=kwargs["destination_id"],
            priority=kwargs.get("priority", 0),
            notes=kwargs.get("notes"),
        )
        self.tasks[task.id] = task
        return task

    async def list_tasks(self, status=None, limit=100, offset=0):
        tasks = list(self.tasks.values())
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        return tasks[offset : offset + limit]

    async def cancel_task(self, task_id):
        if self.cancel_error is not None:
            raise self.cancel_error
        task = self.tasks[task_id]
        cancelled = replace(task, status="cancelled")
        self.tasks[task_id] = cancelled
        return cancelled

    async def get_queue_status(self):
        return self.summary


class FakeStateMonitor:
    def __init__(self, current_state=None, poll_state=None):
        self.current_state = current_state
        self.poll_state = poll_state
        self.poll_called = False

    async def get_current_state(self):
        return self.current_state

    async def poll_once(self):
        self.poll_called = True
        return self.poll_state


class FakeSDKAdapter:
    def __init__(self, *, passive_result: bool = True, stand_up_result: bool = True):
        self.passive_result = passive_result
        self.stand_up_result = stand_up_result
        self.passive_calls = 0
        self.stand_up_calls = 0

    async def passive(self):
        self.passive_calls += 1
        return self.passive_result

    async def stand_up(self):
        self.stand_up_calls += 1
        return self.stand_up_result


class FakeRouteStore:
    def __init__(self):
        self.routes = {"A_TO_QA": make_route()}

    async def list_routes(self, active=None):
        routes = list(self.routes.values())
        if active is not None:
            routes = [route for route in routes if route.active is active]
        return routes

    async def get_route_definition(self, route_id):
        from navigation.route_store import RouteNotFoundError

        if route_id not in self.routes:
            raise RouteNotFoundError(f"Unknown route: {route_id}")
        return self.routes[route_id]

    async def upsert_route(self, route, persist=True):
        self.routes[route.id] = route
        return route


@pytest.fixture
def rest_client(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from core.config import AppConfig, AuthSection

    import api.auth as auth_module
    import api.rest as rest_module

    config = AppConfig(
        auth=AuthSection(
            operator_token=TEST_OPERATOR_TOKEN,
            qa_token=TEST_QA_TOKEN,
            supervisor_token=TEST_SUPERVISOR_TOKEN,
        )
    )
    queue = FakeTaskQueue()
    state_monitor = FakeStateMonitor()
    sdk = FakeSDKAdapter()
    route_store = FakeRouteStore()

    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    monkeypatch.setattr(rest_module, "get_task_queue_dep", lambda: queue)
    monkeypatch.setattr(rest_module, "get_state_monitor_dep", lambda: state_monitor)
    monkeypatch.setattr(rest_module, "get_sdk_adapter_dep", lambda: sdk)
    monkeypatch.setattr(rest_module, "get_route_store_dep", lambda: route_store)

    app = rest_module.create_app()
    return TestClient(app), queue, state_monitor, sdk, route_store, rest_module


def test_health_endpoint(rest_client) -> None:
    client, *_ = rest_client

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_task_success(rest_client) -> None:
    client, queue, *_ = rest_client

    response = client.post(
        "/tasks",
        json={"station_id": "A", "destination_id": "QA", "priority": 1, "notes": "urgent"},
        headers=build_auth_header(TEST_OPERATOR_TOKEN),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["station_id"] == "A"
    assert body["destination_id"] == "QA"
    assert body["priority"] == 1
    assert queue.tasks[body["id"]].notes == "urgent"


def test_create_task_invalid_request_returns_422_or_400(rest_client) -> None:
    client, *_ = rest_client

    response = client.post("/tasks", json={"station_id": "A"}, headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code in {400, 422}


def test_create_task_rejects_missing_auth(rest_client) -> None:
    client, *_ = rest_client

    response = client.post("/tasks", json={"station_id": "A", "destination_id": "QA"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_create_task_accepts_supervisor_token(rest_client) -> None:
    client, queue, *_ = rest_client

    response = client.post(
        "/tasks",
        json={"station_id": "A", "destination_id": "QA", "priority": 2},
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["priority"] == 2
    assert queue.tasks[body["id"]].destination_id == "QA"


def test_list_tasks(rest_client) -> None:
    client, queue, *_ = rest_client
    queue.tasks["task-1"] = make_task_record("task-1")
    queue.tasks["task-2"] = make_task_record("task-2", status="cancelled")

    response = client.get("/tasks", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_cancel_task_success(rest_client) -> None:
    client, queue, *_ = rest_client
    queue.tasks["task-1"] = make_task_record("task-1")

    response = client.delete("/tasks/task-1", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_cancel_task_not_found_returns_404(rest_client) -> None:
    client, queue, *_ , rest_module = rest_client
    queue.cancel_error = rest_module.TaskQueueError("Task not found: task-404")

    response = client.delete("/tasks/task-404", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 404


def test_cancel_task_invalid_transition_returns_409(rest_client) -> None:
    client, queue, *_ , rest_module = rest_client
    queue.cancel_error = rest_module.InvalidTaskTransitionError("Invalid task transition")

    response = client.delete("/tasks/task-1", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 409


def test_queue_status(rest_client) -> None:
    client, queue, *_ = rest_client
    from tasks.queue import QueueSummary

    queue.summary = QueueSummary(
        total=3,
        queued=1,
        dispatched=1,
        awaiting_load=0,
        in_transit=0,
        awaiting_unload=0,
        completed=1,
        failed=0,
        cancelled=0,
    )

    response = client.get("/queue/status", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    assert response.json()["total"] == 3


def test_quadruped_status_with_current_state(rest_client) -> None:
    client, _, state_monitor, *_ = rest_client
    state_monitor.current_state = make_state()

    response = client.get("/quadruped/status", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    body = response.json()
    assert body["battery_pct"] == 88
    assert body["mode"] == "standing"


def test_quadruped_status_fallback_poll_once(rest_client) -> None:
    client, _, state_monitor, *_ = rest_client
    state_monitor.current_state = None
    state_monitor.poll_state = make_state()

    response = client.get("/quadruped/status", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    assert response.json()["battery_pct"] == 88
    assert state_monitor.poll_called is True


def test_quadruped_status_when_no_state_available(rest_client) -> None:
    client, _, state_monitor, *_ = rest_client
    state_monitor.current_state = None
    state_monitor.poll_state = None

    response = client.get("/quadruped/status", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    assert response.json()["battery_pct"] is None
    assert response.json()["mode"] is None


def test_estop_success(rest_client) -> None:
    client, _, _, sdk, _ , _ = rest_client

    response = client.post("/estop", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    assert response.json()["message"] == "Emergency stop triggered"
    assert sdk.passive_calls == 1


def test_estop_failure_returns_503(rest_client) -> None:
    client, _, _, sdk, _ , _ = rest_client
    sdk.passive_result = False

    response = client.post("/estop", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 503


def test_estop_release_success(rest_client) -> None:
    client, _, _, sdk, _ , _ = rest_client

    response = client.post("/estop/release", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert response.json()["message"] == "Emergency stop released"
    assert sdk.stand_up_calls == 1


def test_estop_release_rejects_operator_token(rest_client) -> None:
    client, *_ = rest_client

    response = client.post("/estop/release", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 403
    assert response.json()["detail"] == "Forbidden"


def test_get_routes(rest_client) -> None:
    client, _, _, _, route_store, _ = rest_client
    route_store.routes["QA_TO_A"] = replace(make_route("QA_TO_A"), origin_id="QA", destination_id="A")

    response = client.get("/routes", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_get_routes_rejects_operator_token(rest_client) -> None:
    client, *_ = rest_client

    response = client.get("/routes", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 403
    assert response.json()["detail"] == "Forbidden"


def test_get_route_by_id(rest_client) -> None:
    client, *_ = rest_client

    response = client.get("/routes/A_TO_QA", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert response.json()["id"] == "A_TO_QA"


def test_get_route_not_found_returns_404(rest_client) -> None:
    client, *_ = rest_client

    response = client.get("/routes/UNKNOWN", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 404


def test_put_route_updates_route(rest_client) -> None:
    client, _, _, _, route_store, _ = rest_client

    response = client.put(
        "/routes/A_TO_QA",
        json={
            "name": "Updated route",
            "origin_id": "A",
            "destination_id": "QA",
            "active": True,
            "metadata": {"notes": "updated"},
            "waypoints": [
                {
                    "name": "dock_exit",
                    "x": 1.0,
                    "y": 1.5,
                    "heading_deg": 45.0,
                    "velocity": 0.3,
                    "hold": True,
                    "metadata": {"zone": "aisle-1"},
                }
            ],
        },
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Updated route"
    assert route_store.routes["A_TO_QA"].waypoints[0].metadata["zone"] == "aisle-1"


def test_put_route_bad_payload_returns_422_or_400(rest_client) -> None:
    client, *_ = rest_client

    response = client.put(
        "/routes/A_TO_QA",
        json={
            "name": "Broken route",
            "origin_id": "A",
            "destination_id": "QA",
            "waypoints": [{"name": "", "x": 0.0, "y": 0.0, "heading_deg": 0.0, "velocity": 0.2, "hold": False}],
        },
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code in {400, 422}
