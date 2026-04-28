from __future__ import annotations

import sys
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


class FakeAsyncService:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class FakeStateMonitor:
    def __init__(self, state=None):
        self.state = state

    async def get_current_state(self):
        return self.state


class FakeRobotRegistry:
    def __init__(self, platforms=None):
        self._platforms = {platform.robot_id: platform for platform in platforms or []}

    def all(self):
        return list(self._platforms.values())

    def get(self, robot_id: str):
        return self._platforms[robot_id]


def make_state(*, connected: bool, battery_pct: int):
    return SimpleNamespace(connection_ok=connected, battery_pct=battery_pct)


def make_robot_platform(robot_id: str, *, role: str, state=None):
    return SimpleNamespace(
        robot_id=robot_id,
        state_monitor=FakeStateMonitor(state),
        config=SimpleNamespace(role=role, connection=SimpleNamespace(role=role)),
    )


@pytest.fixture
def health_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from fastapi.testclient import TestClient

    from core.config import AppConfig, AuthSection
    from shared.audit.audit_models import AuditEvent
    from shared.audit.audit_store import AuditStore

    import api.auth as auth_module
    import api.rest as rest_module
    import shared.observability.health as health_module
    import shared.observability.metrics as metrics_module

    config = AppConfig(
        auth=AuthSection(
            operator_token=TEST_OPERATOR_TOKEN,
            qa_token=TEST_QA_TOKEN,
            supervisor_token=TEST_SUPERVISOR_TOKEN,
        )
    )

    platforms = [
        make_robot_platform("logistics_01", role="logistics", state=make_state(connected=True, battery_pct=80)),
        make_robot_platform("patrol_01", role="patrol", state=make_state(connected=False, battery_pct=70)),
    ]
    robot_registry = FakeRobotRegistry(platforms)
    audit_store = AuditStore(tmp_path / "audit.jsonl")
    audit_store.append(AuditEvent(event_type="estop_triggered", severity="warning", robot_id="logistics_01"))
    audit_store.append(AuditEvent(event_type="provisioning_failed", severity="error", robot_id="logistics_01"))
    audit_store.append(AuditEvent(event_type="battery_critical", severity="critical", robot_id="patrol_01"))

    async def _noop_async() -> None:
        return None

    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    monkeypatch.setattr(rest_module, "startup_system", _noop_async)
    monkeypatch.setattr(rest_module, "shutdown_system", _noop_async)
    monkeypatch.setattr(rest_module, "get_ws_broker", lambda: FakeAsyncService())
    monkeypatch.setattr(rest_module, "get_alert_manager", lambda: FakeAsyncService())
    monkeypatch.setattr(health_module, "get_robot_registry", lambda: robot_registry)
    monkeypatch.setattr(metrics_module, "get_robot_registry", lambda: robot_registry)
    monkeypatch.setattr(health_module, "get_audit_store", lambda: audit_store)
    monkeypatch.setattr(metrics_module, "get_audit_store", lambda: audit_store)

    app = rest_module.create_app()
    return TestClient(app), robot_registry, audit_store, health_module, metrics_module


def test_health_returns_structure_and_degraded_status(health_client) -> None:
    client, *_ = health_client

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["runtime"]["registered_robot_count"] == 2
    assert body["audit"]["available"] is True
    assert body["provisioning"]["available"] is True
    assert {robot["robot_id"] for robot in body["robots"]} == {"logistics_01", "patrol_01"}


def test_health_handles_empty_registry_safely(health_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _robot_registry, _audit_store, health_module, metrics_module = health_client
    empty_registry = FakeRobotRegistry([])
    monkeypatch.setattr(health_module, "get_robot_registry", lambda: empty_registry)
    monkeypatch.setattr(metrics_module, "get_robot_registry", lambda: empty_registry)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["runtime"]["registered_robot_count"] == 0
    assert body["robots"] == []


def test_health_robots_returns_robot_id_and_role(health_client) -> None:
    client, *_ = health_client

    response = client.get("/health/robots", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    body = response.json()
    assert body == [
        {
            "robot_id": "logistics_01",
            "role": "logistics",
            "connected": True,
            "battery_pct": 80,
            "status": "ok",
        },
        {
            "robot_id": "patrol_01",
            "role": "patrol",
            "connected": False,
            "battery_pct": 70,
            "status": "degraded",
        },
    ]


def test_metrics_returns_robot_counts_by_role_and_connection(health_client) -> None:
    client, *_ = health_client

    response = client.get("/metrics", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    body = response.json()
    assert body["registered_robot_count"] == 2
    assert body["robots_by_role"] == {"logistics": 1, "patrol": 1}
    assert body["connected_robot_count"] == 1
    assert body["disconnected_robot_count"] == 1


def test_metrics_includes_audit_counts(health_client) -> None:
    client, *_ = health_client

    response = client.get("/metrics", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    body = response.json()
    assert body["audit_event_count"] == 3
    assert body["audit_error_count"] == 1
    assert body["audit_critical_count"] == 1


def test_metrics_handles_missing_audit_file(health_client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client, _robot_registry, _audit_store, _health_module, metrics_module = health_client
    from shared.audit.audit_store import AuditStore

    missing_store = AuditStore(tmp_path / "missing-audit.jsonl")
    monkeypatch.setattr(metrics_module, "get_audit_store", lambda: missing_store)

    response = client.get("/metrics", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    body = response.json()
    assert body["audit_event_count"] == 0
    assert body["audit_error_count"] == 0
    assert body["audit_critical_count"] == 0


def test_health_and_metrics_do_not_expose_secrets(health_client) -> None:
    client, *_ = health_client

    health_response = client.get("/health")
    metrics_response = client.get("/metrics", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert "password" not in health_response.text.lower()
    assert "secret" not in health_response.text.lower()
    assert "password" not in metrics_response.text.lower()
    assert "secret" not in metrics_response.text.lower()
