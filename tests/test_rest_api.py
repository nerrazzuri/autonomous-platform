from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

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

    async def get_task(self, task_id):
        from tasks.queue import TaskQueueError

        task = self.tasks.get(task_id)
        if task is None:
            raise TaskQueueError(f"Task not found: {task_id}")
        return task


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


class FakeDispatcher:
    def __init__(self, *, active_task_id: str | None = None, error: Exception | None = None):
        self.active_task_id = active_task_id
        self.error = error
        self._active_tasks: dict[str, str] = {}

    async def get_state(self):
        if self.error is not None:
            raise self.error
        return SimpleNamespace(active_task_id=self.active_task_id)


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


class FakeEventBus:
    def __init__(self):
        self.published: list[dict[str, object]] = []
        self.publish_error: Exception | None = None

    async def publish(self, event_name, payload=None, *, source=None, task_id=None, correlation_id=None):
        if self.publish_error is not None:
            raise self.publish_error
        event = {
            "event_name": event_name.value if hasattr(event_name, "value") else str(event_name),
            "payload": dict(payload or {}),
            "source": source,
            "task_id": task_id,
            "correlation_id": correlation_id,
        }
        self.published.append(event)
        return event


class FakeRobotRegistry:
    def __init__(self, platforms=None):
        self._platforms = {platform.robot_id: platform for platform in platforms or []}

    def get(self, robot_id: str):
        from shared.quadruped.robot_registry import RobotNotFoundError

        try:
            return self._platforms[robot_id]
        except KeyError as exc:
            raise RobotNotFoundError(robot_id) from exc

    def all(self):
        return list(self._platforms.values())


def make_robot_platform(
    robot_id: str,
    *,
    role: str | None = "logistics",
    state_monitor=None,
    sdk_adapter=None,
    display_name: str | None = None,
):
    return SimpleNamespace(
        robot_id=robot_id,
        state_monitor=state_monitor or FakeStateMonitor(),
        sdk_adapter=sdk_adapter or FakeSDKAdapter(),
        config=SimpleNamespace(
            display_name=display_name,
            role=role,
            connection=SimpleNamespace(robot_id=robot_id, role=role),
        ),
    )


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
    dispatcher = FakeDispatcher()
    event_bus = FakeEventBus()
    robot_registry = FakeRobotRegistry()

    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    monkeypatch.setattr(rest_module, "get_task_queue_dep", lambda: queue)
    monkeypatch.setattr(rest_module, "get_state_monitor_dep", lambda: state_monitor)
    monkeypatch.setattr(rest_module, "get_dispatcher_dep", lambda: dispatcher)
    monkeypatch.setattr(rest_module, "get_sdk_adapter_dep", lambda: sdk)
    monkeypatch.setattr(rest_module, "get_route_store_dep", lambda: route_store)
    monkeypatch.setattr(rest_module, "get_event_bus", lambda: event_bus)
    monkeypatch.setattr(rest_module, "get_robot_registry", lambda: robot_registry)
    monkeypatch.setattr(rest_module, "startup_system", _noop_async)
    monkeypatch.setattr(rest_module, "shutdown_system", _noop_async)

    app = rest_module.create_app()
    rest_module._test_robot_registry = robot_registry
    return TestClient(app), queue, state_monitor, sdk, route_store, dispatcher, event_bus, rest_module


async def _noop_async() -> None:
    return None


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


def test_confirm_load_success_publishes_event(rest_client) -> None:
    client, queue, *_ , event_bus, _rest_module = rest_client
    queue.tasks["task-1"] = make_task_record("task-1", station_id="A", destination_id="QA", status="awaiting_load")

    response = client.post("/tasks/task-1/confirm-load", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    assert response.json() == {"message": "Load confirmed"}
    assert event_bus.published == [
        {
            "event_name": "human.confirmed_load",
            "payload": {
                "task_id": "task-1",
                "station_id": "A",
                "destination_id": "QA",
                "status": "awaiting_load",
            },
            "source": "api.rest",
            "task_id": "task-1",
            "correlation_id": None,
        }
    ]


def test_confirm_unload_success_publishes_event(rest_client) -> None:
    client, queue, *_ , event_bus, _rest_module = rest_client
    queue.tasks["task-2"] = make_task_record("task-2", station_id="A", destination_id="QA", status="awaiting_unload")

    response = client.post("/tasks/task-2/confirm-unload", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    assert response.json() == {"message": "Unload confirmed"}
    assert event_bus.published == [
        {
            "event_name": "human.confirmed_unload",
            "payload": {
                "task_id": "task-2",
                "station_id": "A",
                "destination_id": "QA",
                "status": "awaiting_unload",
            },
            "source": "api.rest",
            "task_id": "task-2",
            "correlation_id": None,
        }
    ]


def test_confirm_load_not_found_returns_404(rest_client) -> None:
    client, *_ = rest_client

    response = client.post("/tasks/task-404/confirm-load", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 404


def test_confirm_unload_not_found_returns_404(rest_client) -> None:
    client, *_ = rest_client

    response = client.post("/tasks/task-404/confirm-unload", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 404


def test_confirm_load_wrong_status_returns_409(rest_client) -> None:
    client, queue, *_ = rest_client
    queue.tasks["task-1"] = make_task_record("task-1", status="queued")

    response = client.post("/tasks/task-1/confirm-load", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 409


def test_confirm_unload_wrong_status_returns_409(rest_client) -> None:
    client, queue, *_ = rest_client
    queue.tasks["task-2"] = make_task_record("task-2", status="in_transit")

    response = client.post("/tasks/task-2/confirm-unload", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 409


def test_confirm_load_requires_auth(rest_client) -> None:
    client, queue, *_ = rest_client
    queue.tasks["task-1"] = make_task_record("task-1")

    response = client.post("/tasks/task-1/confirm-load")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_confirm_unload_requires_auth(rest_client) -> None:
    client, queue, *_ = rest_client
    queue.tasks["task-1"] = make_task_record("task-1")

    response = client.post("/tasks/task-1/confirm-unload")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


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


def test_quadruped_status_includes_active_task_id(rest_client) -> None:
    client, _, state_monitor, *_, dispatcher, _event_bus, _rest_module = rest_client
    state_monitor.current_state = make_state()
    dispatcher.active_task_id = "task-42"

    response = client.get("/quadruped/status", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    assert response.json()["active_task_id"] == "task-42"


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


def test_quadruped_status_dispatcher_failure_still_returns_response(rest_client) -> None:
    client, _, state_monitor, *_, dispatcher, _event_bus, _rest_module = rest_client
    state_monitor.current_state = make_state()
    dispatcher.error = RuntimeError("dispatcher status unavailable")

    response = client.get("/quadruped/status", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    body = response.json()
    assert body["battery_pct"] == 88
    assert body["active_task_id"] is None


def test_estop_success(rest_client) -> None:
    client, _, _, sdk, *_ = rest_client

    response = client.post("/estop", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    assert response.json()["message"] == "Emergency stop triggered"
    assert sdk.passive_calls == 1


def test_estop_failure_returns_503(rest_client) -> None:
    client, _, _, sdk, *_ = rest_client
    sdk.passive_result = False

    response = client.post("/estop", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 503


def test_estop_release_success(rest_client) -> None:
    client, _, _, sdk, *_ = rest_client

    response = client.post("/estop/release", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert response.json()["message"] == "Emergency stop released"
    assert sdk.stand_up_calls == 1


def test_estop_release_rejects_operator_token(rest_client) -> None:
    client, *_ = rest_client

    response = client.post("/estop/release", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 403
    assert response.json()["detail"] == "Forbidden"


def test_get_robots_returns_registered_logistics_robots(rest_client) -> None:
    client, _, _, _, _, dispatcher, _event_bus, rest_module = rest_client
    registry = rest_module._test_robot_registry
    robot_01_monitor = FakeStateMonitor(current_state=make_state())
    robot_02_state = make_state()
    robot_02_state = replace(robot_02_state, battery_pct=64, position=(4.0, 5.0, 0.0), control_mode=7, connection_ok=False)
    robot_02_monitor = FakeStateMonitor(current_state=robot_02_state)
    registry._platforms = {
        "logistics_01": make_robot_platform(
            "logistics_01",
            display_name="Logistics Robot 1",
            state_monitor=robot_01_monitor,
        ),
        "logistics_02": make_robot_platform(
            "logistics_02",
            display_name="Logistics Robot 2",
            state_monitor=robot_02_monitor,
        ),
        "patrol_01": make_robot_platform(
            "patrol_01",
            role="patrol",
            state_monitor=FakeStateMonitor(current_state=make_state()),
        ),
    }
    dispatcher._active_tasks = {"logistics_01": "task-a", "logistics_02": "task-b"}

    response = client.get("/robots", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    assert response.json() == [
        {
            "robot_id": "logistics_01",
            "display_name": "Logistics Robot 1",
            "role": "logistics",
            "connected": True,
            "battery_pct": 88,
            "position": {"x": 1.0, "y": 2.0, "z": 0.0},
            "active_task_id": "task-a",
            "mode": 3,
        },
        {
            "robot_id": "logistics_02",
            "display_name": "Logistics Robot 2",
            "role": "logistics",
            "connected": False,
            "battery_pct": 64,
            "position": {"x": 4.0, "y": 5.0, "z": 0.0},
            "active_task_id": "task-b",
            "mode": 7,
        },
    ]


def test_get_robot_status_returns_requested_robot(rest_client) -> None:
    client, _, _, _, _, dispatcher, _event_bus, rest_module = rest_client
    registry = rest_module._test_robot_registry
    robot_01_monitor = FakeStateMonitor(current_state=make_state())
    robot_02_state = replace(make_state(), battery_pct=51, position=(9.0, 8.0, 0.0), control_mode=12)
    robot_02_monitor = FakeStateMonitor(current_state=robot_02_state)
    registry._platforms = {
        "logistics_01": make_robot_platform("logistics_01", state_monitor=robot_01_monitor),
        "logistics_02": make_robot_platform("logistics_02", state_monitor=robot_02_monitor),
    }
    dispatcher._active_tasks = {"logistics_01": "task-a", "logistics_02": "task-b"}

    response = client.get("/robots/logistics_02/status", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    assert response.json() == {
        "robot_id": "logistics_02",
        "display_name": None,
        "role": "logistics",
        "connected": True,
        "battery_pct": 51,
        "position": {"x": 9.0, "y": 8.0, "z": 0.0},
        "active_task_id": "task-b",
        "mode": 12,
    }


def test_get_robot_status_unknown_robot_returns_404(rest_client) -> None:
    client, *_ = rest_client

    response = client.get("/robots/missing/status", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 404


def test_robot_estop_affects_only_target_robot(rest_client) -> None:
    client, _, _, _, _, _dispatcher, _event_bus, rest_module = rest_client
    registry = rest_module._test_robot_registry
    robot_01_sdk = FakeSDKAdapter()
    robot_02_sdk = FakeSDKAdapter()
    registry._platforms = {
        "logistics_01": make_robot_platform("logistics_01", sdk_adapter=robot_01_sdk),
        "logistics_02": make_robot_platform("logistics_02", sdk_adapter=robot_02_sdk),
    }

    response = client.post("/robots/logistics_01/estop", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    assert robot_01_sdk.passive_calls == 1
    assert robot_02_sdk.passive_calls == 0


def test_robot_estop_release_affects_only_target_robot(rest_client) -> None:
    client, _, _, _, _, _dispatcher, _event_bus, rest_module = rest_client
    registry = rest_module._test_robot_registry
    robot_01_sdk = FakeSDKAdapter()
    robot_02_sdk = FakeSDKAdapter()
    registry._platforms = {
        "logistics_01": make_robot_platform("logistics_01", sdk_adapter=robot_01_sdk),
        "logistics_02": make_robot_platform("logistics_02", sdk_adapter=robot_02_sdk),
    }

    response = client.post("/robots/logistics_02/estop/release", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert robot_01_sdk.stand_up_calls == 0
    assert robot_02_sdk.stand_up_calls == 1


def test_quadruped_status_uses_first_registered_robot_when_registry_populated(rest_client) -> None:
    client, _, state_monitor, *_rest, dispatcher, _event_bus, rest_module = rest_client
    state_monitor.current_state = None
    robot_state = replace(make_state(), battery_pct=71, position=(7.0, 6.0, 0.0), control_mode=8)
    registry = rest_module._test_robot_registry
    registry._platforms = {
        "logistics_01": make_robot_platform("logistics_01", state_monitor=FakeStateMonitor(current_state=robot_state)),
        "logistics_02": make_robot_platform("logistics_02", state_monitor=FakeStateMonitor(current_state=make_state())),
    }
    dispatcher._active_tasks = {"logistics_01": "task-100"}

    response = client.get("/quadruped/status", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert response.status_code == 200
    assert response.json()["battery_pct"] == 71
    assert response.json()["active_task_id"] == "task-100"
    assert response.json()["control_mode"] == 8


def test_logistics_robots_endpoint_excludes_patrol_and_status_404s_for_patrol(rest_client) -> None:
    client, _, _, _, _, _dispatcher, _event_bus, rest_module = rest_client
    registry = rest_module._test_robot_registry
    registry._platforms = {
        "logistics_01": make_robot_platform("logistics_01", role="logistics", state_monitor=FakeStateMonitor(current_state=make_state())),
        "patrol_01": make_robot_platform("patrol_01", role="patrol", state_monitor=FakeStateMonitor(current_state=make_state())),
    }

    list_response = client.get("/robots", headers=build_auth_header(TEST_OPERATOR_TOKEN))
    status_response = client.get("/robots/patrol_01/status", headers=build_auth_header(TEST_OPERATOR_TOKEN))

    assert list_response.status_code == 200
    assert [item["robot_id"] for item in list_response.json()] == ["logistics_01"]
    assert status_response.status_code == 404


def test_get_routes(rest_client) -> None:
    client, _, _, _, route_store, *_ = rest_client
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
    client, _, _, _, route_store, *_ = rest_client

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
