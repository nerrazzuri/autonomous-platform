from __future__ import annotations

import sys
import time
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


class FakeAsyncService:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class FakeProvisioningBackend:
    def __init__(self) -> None:
        self.scan_result = []
        self.provision_result = None
        self.provision_error: Exception | None = None
        self.list_entries_result: list[dict[str, object]] = []
        self.remove_result: dict[str, object] | None = None

    def scan_wifi_networks(self):
        return self.scan_result

    def provision_quadruped(self, _request):
        if self.provision_error is not None:
            raise self.provision_error
        return self.provision_result

    def list_robot_entries(self, _path):
        return [dict(entry) for entry in self.list_entries_result]

    def remove_robot_entry(self, _robot_id, _path):
        assert self.remove_result is not None
        return dict(self.remove_result)

    def write_robot_entry(self, result, role, _robots_yaml_path, *, display_name=None, sdk_lib_path="sdk/zsl-1"):
        return {
            "robot_id": result.robot_id,
            "display_name": display_name,
            "mac": result.quadruped_mac,
            "quadruped_ip": result.quadruped_ip,
            "role": role,
            "enabled": True,
            "sdk_lib_path": sdk_lib_path,
        }


@pytest.fixture
def audit_api_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from fastapi.testclient import TestClient

    from core.config import AppConfig, AuthSection
    from shared.audit.audit_models import AuditEvent
    from shared.audit.audit_store import AuditStore
    from shared.provisioning.provision_models import ProvisionResult, WifiNetwork

    import api.auth as auth_module
    import api.rest as rest_module

    config = AppConfig(
        auth=AuthSection(
            operator_token=TEST_OPERATOR_TOKEN,
            qa_token=TEST_QA_TOKEN,
            supervisor_token=TEST_SUPERVISOR_TOKEN,
        )
    )
    fake_backend = FakeProvisioningBackend()
    fake_backend.scan_result = [
        WifiNetwork(ssid="D1-Ultra:aa:bb:cc:dd:ee", signal=80, security="WPA2", is_robot_ap=True),
    ]
    fake_backend.provision_result = ProvisionResult(
        success=True,
        robot_id="logistics_01",
        quadruped_mac="aa:bb:cc:dd:ee:01",
        quadruped_ip="192.168.1.50",
        pc_ip="192.168.1.10",
        role="logistics",
    )
    fake_backend.list_entries_result = [
        {
            "robot_id": "logistics_01",
            "display_name": "Logistics Robot 1",
            "mac": "aa:bb:cc:dd:ee:01",
            "quadruped_ip": "192.168.1.50",
            "role": "logistics",
            "enabled": True,
        }
    ]
    fake_backend.remove_result = dict(fake_backend.list_entries_result[0])

    async def _noop_async() -> None:
        return None

    audit_store = AuditStore(tmp_path / "audit.jsonl")

    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    monkeypatch.setattr(rest_module, "startup_system", _noop_async)
    monkeypatch.setattr(rest_module, "shutdown_system", _noop_async)
    monkeypatch.setattr(rest_module, "get_ws_broker", lambda: FakeAsyncService())
    monkeypatch.setattr(rest_module, "get_alert_manager", lambda: FakeAsyncService())
    monkeypatch.setattr(rest_module, "provision_backend", fake_backend)
    monkeypatch.setattr(rest_module, "_get_provisioning_robots_yaml_path", lambda: tmp_path / "robots.yaml")
    monkeypatch.setattr(rest_module, "get_audit_store", lambda: audit_store)
    rest_module._PROVISIONING_JOBS.clear()

    app = rest_module.create_app()
    client = TestClient(app)
    return client, fake_backend, audit_store, AuditEvent


def _poll_job_status(client, job_id: str, *, timeout_seconds: float = 2.0) -> dict[str, object]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = client.get(
            f"/provision/status/{job_id}",
            headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
        )
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"succeeded", "failed"}:
            return body
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for job {job_id}")


def test_get_audit_events_returns_filtered_results(audit_api_client) -> None:
    client, _fake_backend, audit_store, AuditEvent = audit_api_client
    audit_store.append(AuditEvent(event_type="estop_triggered", robot_id="robot_01", severity="warning"))
    audit_store.append(AuditEvent(event_type="estop_released", robot_id="robot_02", severity="info"))

    response = client.get(
        "/audit/events?robot_id=robot_01&severity=warning&limit=5",
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["robot_id"] == "robot_01"
    assert body[0]["event_type"] == "estop_triggered"


def test_successful_provisioning_creates_audit_events_without_password_leak(audit_api_client) -> None:
    client, _fake_backend, audit_store, _AuditEvent = audit_api_client

    response = client.post(
        "/provision/start",
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
        json={
            "quadruped_ap_ssid": "D1-Ultra:aa:bb:cc:dd:ee",
            "target_wifi_ssid": "FACTORY_WIFI",
            "target_wifi_password": "secret-password",
            "role": "logistics",
            "robot_id": "logistics_01",
            "display_name": "Logistics Robot 1",
        },
    )

    assert response.status_code == 200
    status_body = _poll_job_status(client, response.json()["job_id"])
    assert status_body["status"] == "succeeded"
    assert "secret-password" not in response.text
    assert "secret-password" not in str(status_body)

    events = audit_store.list_events(limit=10)
    event_types = {event.event_type for event in events}
    assert "provisioning_started" in event_types
    assert "provisioning_succeeded" in event_types
    assert all("secret-password" not in str(event.metadata) for event in events)


def test_robot_estop_creates_audit_event(audit_api_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _fake_backend, audit_store, _AuditEvent = audit_api_client

    class FakeSDKAdapter:
        async def passive(self):
            return True

    class FakePlatform:
        robot_id = "logistics_01"
        sdk_adapter = FakeSDKAdapter()
        config = type("Config", (), {"role": "logistics", "connection": type("Conn", (), {"role": "logistics"})()})()

    class FakeRobotRegistry:
        def get(self, robot_id: str):
            if robot_id != "logistics_01":
                raise AssertionError("unexpected robot_id")
            return FakePlatform()

        def all(self):
            return [FakePlatform()]

    import api.rest as rest_module

    monkeypatch.setattr(rest_module, "get_robot_registry", lambda: FakeRobotRegistry())

    response = client.post(
        "/robots/logistics_01/estop",
        headers=build_auth_header(TEST_OPERATOR_TOKEN),
    )

    assert response.status_code == 200
    events = audit_store.list_events(robot_id="logistics_01", event_type="estop_triggered", limit=5)
    assert len(events) == 1
    assert events[0].robot_id == "logistics_01"
