from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TEST_OPERATOR_TOKEN = "test-operator-token"
TEST_QA_TOKEN = "test-qa-token"
TEST_SUPERVISOR_TOKEN = "test-supervisor-token"


def build_auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_cycle(
    cycle_id: str = "cycle-1",
    *,
    route_id: str = "PATROL_NORTH_LOOP",
    status: str = "scheduled",
    triggered_by: str = "manual",
    failure_reason: str | None = None,
):
    return SimpleNamespace(
        cycle_id=cycle_id,
        route_id=route_id,
        status=status,
        triggered_by=triggered_by,
        created_at="2026-04-26T00:00:00+00:00",
        started_at="2026-04-26T00:01:00+00:00" if status != "scheduled" else None,
        completed_at="2026-04-26T00:02:00+00:00" if status in {"completed", "failed"} else None,
        waypoints_total=3,
        waypoints_observed=2,
        anomaly_ids=["anom-1"] if status == "completed" else [],
        failure_reason=failure_reason,
    )


def make_anomaly(
    anomaly_id: str = "anom-1",
    *,
    resolved_at: str | None = None,
    resolved_by: str | None = None,
):
    from apps.patrol.observation.anomaly_log import AnomalyRecord

    return AnomalyRecord(
        anomaly_id=anomaly_id,
        cycle_id="cycle-1",
        zone_id="ZONE_NORTH",
        waypoint_name="north_observation_1",
        detected_at="2026-04-26T00:00:00+00:00",
        severity="warning",
        threat_objects_json='[{"label":"wild boar","threat_level":"SUSPICIOUS","confidence":0.84}]',
        confidence_max=0.84,
        resolved_at=resolved_at,
        resolved_by=resolved_by,
        metadata_json='{"source":"camera-1"}',
    )


def make_zone():
    from apps.patrol.observation.zone_config import ZoneDefinition

    return ZoneDefinition(
        zone_id="ZONE_NORTH",
        description="North perimeter",
        normal_objects=["tree"],
        suspicious_objects=["wild boar"],
        threat_objects=["fire"],
    )


def make_route(route_id: str = "PATROL_NORTH_LOOP", *, route_type: str = "patrol"):
    from shared.navigation.route_store import RouteDefinition, Waypoint

    return RouteDefinition(
        id=route_id,
        name="North patrol",
        origin_id="NORTH_START",
        destination_id="NORTH_END",
        active=True,
        metadata={"route_type": route_type, "notes": "sample"},
        waypoints=[
            Waypoint(
                name="wp-1",
                x=1.0,
                y=2.0,
                heading_deg=0.0,
                velocity=0.25,
                hold=False,
                metadata={"observe": True, "zone_id": "ZONE_NORTH"},
            )
        ],
    )


class FakePatrolQueue:
    def __init__(self) -> None:
        self.cycles = {"cycle-1": make_cycle("cycle-1", status="completed")}
        self.submit_error: Exception | None = None

    async def get_cycle_history(self, limit: int = 100):
        return list(self.cycles.values())[:limit]

    async def get_cycle(self, cycle_id: str):
        from apps.patrol.tasks.patrol_queue import PatrolCycleNotFound

        cycle = self.cycles.get(cycle_id)
        if cycle is None:
            raise PatrolCycleNotFound(f"Patrol cycle not found: {cycle_id}")
        return cycle

    async def submit_cycle(self, route_id: str, triggered_by: str = "manual"):
        if self.submit_error is not None:
            raise self.submit_error
        if not route_id.strip():
            raise ValueError("route_id must not be empty")
        if triggered_by not in {"manual", "schedule", "alert"}:
            raise ValueError("triggered_by must be one of: schedule, manual, alert")
        cycle = make_cycle("cycle-created", route_id=route_id, status="scheduled", triggered_by=triggered_by)
        self.cycles[cycle.cycle_id] = cycle
        return cycle


class FakePatrolScheduler:
    def __init__(self) -> None:
        self.suspend_calls: list[str] = []
        self.resume_calls: list[str] = []

    async def get_state(self):
        return SimpleNamespace(suspended=False)

    async def suspend(self, reason: str = "manual suspension") -> None:
        self.suspend_calls.append(reason)

    async def resume(self, reason: str = "manual resume") -> None:
        self.resume_calls.append(reason)


class FakePatrolDispatcher:
    def __init__(self) -> None:
        self.suspend_calls: list[str] = []
        self.resume_calls: list[str] = []

    async def get_state(self):
        return SimpleNamespace(
            suspended=False,
            active_cycle_id="cycle-1",
            active_route_id="PATROL_NORTH_LOOP",
            consecutive_failures=1,
            last_result="completed",
        )

    async def suspend(self, reason: str = "manual suspension") -> None:
        self.suspend_calls.append(reason)

    async def resume(self, reason: str = "manual resume") -> None:
        self.resume_calls.append(reason)


class FakePatrolWatchdog:
    async def get_state(self):
        return SimpleNamespace(suspended=False)


class FakeAnomalyLog:
    def __init__(self) -> None:
        self.resolve_calls: list[tuple[str, str]] = []
        self.anomalies = [make_anomaly("anom-1")]

    async def list_unresolved(self, zone_id: str | None = None):
        if zone_id is None:
            return list(self.anomalies)
        return [item for item in self.anomalies if item.zone_id == zone_id]

    async def resolve(self, anomaly_id: str, resolved_by: str):
        from apps.patrol.observation.anomaly_log import AnomalyNotFoundError

        self.resolve_calls.append((anomaly_id, resolved_by))
        for item in self.anomalies:
            if item.anomaly_id == anomaly_id:
                return make_anomaly(anomaly_id, resolved_at="2026-04-26T00:10:00+00:00", resolved_by=resolved_by)
        raise AnomalyNotFoundError(f"Anomaly not found: {anomaly_id}")


class FakeRouteStore:
    def __init__(self) -> None:
        self.routes = {
            "PATROL_NORTH_LOOP": make_route("PATROL_NORTH_LOOP", route_type="patrol"),
            "LOGISTICS_A_TO_B": make_route("LOGISTICS_A_TO_B", route_type="logistics"),
        }

    async def list_routes(self, active: bool = True):
        return [route for route in self.routes.values() if route.active is active]

    async def upsert_route(self, route, persist: bool = True):
        self.routes[route.id] = route
        return route


class FakeZoneConfig:
    async def list_zones(self):
        return [make_zone()]


class FakeSDKAdapter:
    def __init__(self, *, passive_result: bool = True, stand_up_result: bool = True, fail_passive: bool = False) -> None:
        self.passive_result = passive_result
        self.stand_up_result = stand_up_result
        self.fail_passive = fail_passive
        self.passive_calls = 0
        self.stand_up_calls = 0

    async def passive(self):
        self.passive_calls += 1
        if self.fail_passive:
            raise RuntimeError("sdk passive failed")
        return self.passive_result

    async def stand_up(self):
        self.stand_up_calls += 1
        return self.stand_up_result


class FakeEventBus:
    def __init__(self) -> None:
        self.published: list[dict[str, object]] = []

    async def publish(self, event_name, payload=None, *, source=None, task_id=None, correlation_id=None):
        event = {
            "event_name": event_name.value if hasattr(event_name, "value") else str(event_name),
            "payload": dict(payload or {}),
            "source": source,
            "task_id": task_id,
            "correlation_id": correlation_id,
        }
        self.published.append(event)
        return event


class FakeBroker:
    def __init__(self, calls: list[str] | None = None) -> None:
        self.calls = calls if calls is not None else []

    async def start(self) -> None:
        self.calls.append("ws-start")

    async def stop(self) -> None:
        self.calls.append("ws-stop")


class FakeAlertManager:
    def __init__(self, calls: list[str] | None = None) -> None:
        self.calls = calls if calls is not None else []

    async def start(self) -> None:
        self.calls.append("alert-start")

    async def stop(self) -> None:
        self.calls.append("alert-stop")


@pytest.fixture
def rest_client(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient
    from shared.core.config import AppConfig, AuthSection

    import shared.api.auth as auth_module
    import apps.patrol.api.rest as rest_module

    config = AppConfig(
        auth=AuthSection(
            operator_token=TEST_OPERATOR_TOKEN,
            qa_token=TEST_QA_TOKEN,
            supervisor_token=TEST_SUPERVISOR_TOKEN,
        )
    )
    queue = FakePatrolQueue()
    scheduler = FakePatrolScheduler()
    dispatcher = FakePatrolDispatcher()
    watchdog = FakePatrolWatchdog()
    anomaly_log = FakeAnomalyLog()
    route_store = FakeRouteStore()
    zone_config = FakeZoneConfig()
    sdk = FakeSDKAdapter()
    event_bus = FakeEventBus()

    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    monkeypatch.setattr(rest_module, "get_patrol_queue_dep", lambda: queue)
    monkeypatch.setattr(rest_module, "get_patrol_scheduler_dep", lambda: scheduler)
    monkeypatch.setattr(rest_module, "get_patrol_dispatcher_dep", lambda: dispatcher)
    monkeypatch.setattr(rest_module, "get_patrol_watchdog_dep", lambda: watchdog)
    monkeypatch.setattr(rest_module, "get_anomaly_log_dep", lambda: anomaly_log)
    monkeypatch.setattr(rest_module, "get_route_store_dep", lambda: route_store)
    monkeypatch.setattr(rest_module, "get_zone_config_dep", lambda: zone_config)
    monkeypatch.setattr(rest_module, "get_sdk_adapter_dep", lambda: sdk)
    monkeypatch.setattr(rest_module, "get_event_bus", lambda: event_bus)
    monkeypatch.setattr(rest_module, "startup_system", _noop_async)
    monkeypatch.setattr(rest_module, "shutdown_system", _noop_async)
    monkeypatch.setattr(rest_module, "get_ws_broker", lambda: FakeBroker())
    monkeypatch.setattr(rest_module, "get_alert_manager", lambda: FakeAlertManager())

    app = rest_module.create_app()
    return TestClient(app), queue, scheduler, dispatcher, watchdog, anomaly_log, route_store, zone_config, sdk, event_bus, rest_module


async def _noop_async() -> None:
    return None


def test_health_endpoint(rest_client) -> None:
    client, *_ = rest_client

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "patrol"}


def test_patrol_status(rest_client) -> None:
    client, *_ = rest_client

    response = client.get("/patrol/status", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert response.json() == {
        "running": False,
        "scheduler_suspended": False,
        "dispatcher_suspended": False,
        "active_cycle_id": "cycle-1",
        "active_route_id": "PATROL_NORTH_LOOP",
        "consecutive_failures": 1,
        "watchdog_suspended": False,
        "last_result": "completed",
    }


def test_list_cycles(rest_client) -> None:
    client, queue, *_ = rest_client
    queue.cycles["cycle-2"] = make_cycle("cycle-2", status="failed", failure_reason="blocked")

    response = client.get("/patrol/cycles?status=failed", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["cycle_id"] == "cycle-2"
    assert body[0]["status"] == "failed"


def test_get_cycle(rest_client) -> None:
    client, *_ = rest_client

    response = client.get("/patrol/cycles/cycle-1", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert response.json()["cycle_id"] == "cycle-1"


def test_get_cycle_not_found_404(rest_client) -> None:
    client, *_ = rest_client

    response = client.get("/patrol/cycles/missing", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 404


def test_trigger_cycle_success(rest_client) -> None:
    client, queue, *_ = rest_client

    response = client.post(
        "/patrol/trigger",
        json={"route_id": "PATROL_NORTH_LOOP", "triggered_by": "manual"},
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["cycle_id"] == "cycle-created"
    assert "cycle-created" in queue.cycles


def test_trigger_cycle_invalid_returns_400(rest_client) -> None:
    client, *_ = rest_client

    response = client.post(
        "/patrol/trigger",
        json={"route_id": " ", "triggered_by": "manual"},
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code in {400, 422}


def test_suspend_patrol(rest_client) -> None:
    client, _queue, scheduler, dispatcher, _watchdog, _anomaly_log, _route_store, _zone_config, _sdk, event_bus, _module = rest_client

    response = client.post(
        "/patrol/suspend",
        json={"reason": "manual hold"},
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Patrol suspended"}
    assert scheduler.suspend_calls == ["manual hold"]
    assert dispatcher.suspend_calls == ["manual hold"]
    assert event_bus.published[-1]["event_name"] == "patrol.suspended"


def test_resume_patrol(rest_client) -> None:
    client, _queue, scheduler, dispatcher, _watchdog, _anomaly_log, _route_store, _zone_config, _sdk, event_bus, _module = rest_client

    response = client.post("/patrol/resume", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert response.json() == {"message": "Patrol resumed"}
    assert scheduler.resume_calls == ["manual resume"]
    assert dispatcher.resume_calls == ["manual resume"]
    assert event_bus.published[-1]["event_name"] == "patrol.resumed"


def test_list_unresolved_anomalies(rest_client) -> None:
    client, *_ = rest_client

    response = client.get("/patrol/anomalies", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert response.json()[0]["anomaly_id"] == "anom-1"


def test_resolve_anomaly_success(rest_client) -> None:
    client, _queue, _scheduler, _dispatcher, _watchdog, anomaly_log, *_rest = rest_client

    response = client.post(
        "/patrol/anomalies/anom-1/resolve",
        json={"resolved_by": "supervisor"},
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["resolved_by"] == "supervisor"
    assert anomaly_log.resolve_calls == [("anom-1", "supervisor")]


def test_resolve_anomaly_not_found_404(rest_client) -> None:
    client, *_ = rest_client

    response = client.post(
        "/patrol/anomalies/missing/resolve",
        json={"resolved_by": "supervisor"},
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code == 404


def test_get_patrol_routes(rest_client) -> None:
    client, *_ = rest_client

    response = client.get("/patrol/routes", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == "PATROL_NORTH_LOOP"


def test_upsert_patrol_route(rest_client) -> None:
    client, _queue, _scheduler, _dispatcher, _watchdog, _anomaly_log, route_store, *_rest = rest_client

    response = client.post(
        "/patrol/routes",
        json={
            "id": "PATROL_SOUTH_LOOP",
            "name": "South patrol",
            "origin_id": "SOUTH_START",
            "destination_id": "SOUTH_END",
            "active": True,
            "metadata": {},
            "waypoints": [
                {
                    "name": "wp-1",
                    "x": 1.0,
                    "y": 2.0,
                    "heading_deg": 0.0,
                    "velocity": 0.25,
                    "hold": False,
                    "metadata": {"observe": True, "zone_id": "ZONE_NORTH"},
                }
            ],
        },
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["metadata"]["route_type"] == "patrol"
    assert route_store.routes["PATROL_SOUTH_LOOP"].metadata["route_type"] == "patrol"


def test_get_zones(rest_client) -> None:
    client, *_ = rest_client

    response = client.get("/patrol/zones", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert response.json()[0]["zone_id"] == "ZONE_NORTH"


def test_estop_success(rest_client) -> None:
    client, _queue, _scheduler, _dispatcher, _watchdog, _anomaly_log, _route_store, _zone_config, sdk, event_bus, _module = rest_client

    response = client.post("/estop", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert response.json() == {"message": "Emergency stop triggered"}
    assert sdk.passive_calls == 1
    assert event_bus.published[-1]["event_name"] == "estop.triggered"


def test_estop_failure_503(rest_client) -> None:
    client, _queue, _scheduler, _dispatcher, _watchdog, _anomaly_log, _route_store, _zone_config, sdk, _event_bus, _module = rest_client
    sdk.passive_result = False

    response = client.post("/estop", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 503


def test_estop_release_success(rest_client) -> None:
    client, _queue, _scheduler, _dispatcher, _watchdog, _anomaly_log, _route_store, _zone_config, sdk, _event_bus, _module = rest_client

    response = client.post("/estop/release", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert response.json() == {"message": "Emergency stop released"}
    assert sdk.stand_up_calls == 1


def test_auth_required_for_patrol_endpoints(rest_client) -> None:
    client, *_ = rest_client

    assert client.get("/health").status_code == 200
    assert client.get("/patrol/status").status_code == 401
    assert client.get("/patrol/cycles").status_code == 401
    assert client.post("/patrol/trigger", json={"route_id": "PATROL_NORTH_LOOP"}).status_code == 401
    assert client.post("/estop").status_code == 401


def test_lifespan_starts_and_stops_runtime_broker_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.patrol.api.rest as rest_module

    calls: list[str] = []

    async def startup_runtime() -> None:
        calls.append("runtime-start")

    async def shutdown_runtime() -> None:
        calls.append("runtime-stop")

    monkeypatch.setattr(rest_module, "startup_system", startup_runtime)
    monkeypatch.setattr(rest_module, "shutdown_system", shutdown_runtime)
    monkeypatch.setattr(rest_module, "get_ws_broker", lambda: FakeBroker(calls))
    monkeypatch.setattr(rest_module, "get_alert_manager", lambda: FakeAlertManager(calls))

    app = rest_module.create_app()

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200

    assert calls == [
        "runtime-start",
        "ws-start",
        "alert-start",
        "alert-stop",
        "ws-stop",
        "runtime-stop",
    ]


def test_websocket_route_registered(rest_client) -> None:
    client, *_ = rest_client

    websocket_routes = {route.path for route in client.app.routes if getattr(route, "path", None) == "/ws"}

    assert "/ws" in websocket_routes
