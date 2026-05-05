from __future__ import annotations

import sys
import time
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


class FakeProvisioningBackend:
    def __init__(self) -> None:
        self.scan_result = []
        self.scan_error: Exception | None = None
        self.provision_result = None
        self.provision_error: Exception | None = None
        self.list_entries_result: list[dict[str, object]] = []
        self.list_entries_error: Exception | None = None
        self.remove_result: dict[str, object] | None = None
        self.remove_error: Exception | None = None
        self.last_request = None
        self.write_calls: list[dict[str, object]] = []

    def scan_wifi_networks(self):
        if self.scan_error is not None:
            raise self.scan_error
        return self.scan_result

    def provision_quadruped(self, request):
        self.last_request = request
        if self.provision_error is not None:
            raise self.provision_error
        return self.provision_result

    def list_robot_entries(self, _path):
        if self.list_entries_error is not None:
            raise self.list_entries_error
        return [dict(entry) for entry in self.list_entries_result]

    def remove_robot_entry(self, _robot_id, _path):
        if self.remove_error is not None:
            raise self.remove_error
        assert self.remove_result is not None
        return dict(self.remove_result)

    def write_robot_entry(self, result, role, robots_yaml_path, *, display_name=None, sdk_lib_path="sdk/zsl-1"):
        self.write_calls.append(
            {
                "robot_id": result.robot_id,
                "role": role,
                "display_name": display_name,
                "sdk_lib_path": sdk_lib_path,
                "robots_yaml_path": str(robots_yaml_path),
            }
        )
        return {
            "robot_id": result.robot_id,
            "display_name": display_name,
            "mac": result.quadruped_mac,
            "quadruped_ip": result.quadruped_ip,
            "role": role,
            "enabled": True,
        }


@pytest.fixture
def provision_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from fastapi.testclient import TestClient

    from core.config import AppConfig, AuthSection
    from shared.provisioning.provision_backend import ProvisioningError
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
        WifiNetwork(ssid="FACTORY_WIFI", signal=70, security="WPA2", is_robot_ap=False),
    ]
    fake_backend.provision_result = ProvisionResult(
        success=True,
        robot_id="logistics_01",
        quadruped_mac="aa:bb:cc:dd:ee:01",
        quadruped_ip="192.168.1.50",
        pc_ip="192.168.1.10",
        role="logistics",
    )
    fake_backend.remove_result = {
        "robot_id": "logistics_01",
        "display_name": "Logistics Robot 1",
        "mac": "aa:bb:cc:dd:ee:01",
        "quadruped_ip": "192.168.1.50",
        "role": "logistics",
        "enabled": True,
    }

    async def _noop_async() -> None:
        return None

    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    monkeypatch.setattr(rest_module, "startup_system", _noop_async)
    monkeypatch.setattr(rest_module, "shutdown_system", _noop_async)
    monkeypatch.setattr(rest_module, "get_ws_broker", lambda: FakeAsyncService())
    monkeypatch.setattr(rest_module, "get_alert_manager", lambda: FakeAsyncService())
    monkeypatch.setattr(rest_module, "provision_backend", fake_backend)
    monkeypatch.setattr(rest_module, "_get_provisioning_robots_yaml_path", lambda: tmp_path / "robots.yaml")
    rest_module._PROVISIONING_JOBS.clear()

    app = rest_module.create_app()
    client = TestClient(app)
    return client, fake_backend, rest_module, tmp_path, ProvisioningError


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


def test_get_provision_scan_returns_mocked_networks(provision_client) -> None:
    client, *_ = provision_client

    response = client.get("/provision/scan", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert response.json() == [
        {
            "ssid": "D1-Ultra:aa:bb:cc:dd:ee",
            "signal": 80,
            "security": "WPA2",
            "is_robot_ap": True,
        },
        {
            "ssid": "FACTORY_WIFI",
            "signal": 70,
            "security": "WPA2",
            "is_robot_ap": False,
        },
    ]


def test_get_provision_scan_handles_scan_failure(provision_client) -> None:
    client, fake_backend, _rest_module, _tmp_path, ProvisioningError = provision_client
    fake_backend.scan_error = ProvisioningError("nmcli unavailable")

    response = client.get("/provision/scan", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 503
    assert "nmcli unavailable" in response.json()["detail"]


def test_post_provision_start_returns_job_id_and_hides_password(provision_client) -> None:
    client, _fake_backend, *_ = provision_client

    response = client.post(
        "/provision/start",
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
        json={
            "quadruped_ap_ssid": "D1-Ultra:aa:bb:cc:dd:ee",
            "target_wifi_ssid": "FACTORY_WIFI",
            "target_wifi_password": "secret",
            "role": "logistics",
            "pc_wifi_iface": "wlan0",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["job_id"]
    assert "secret" not in response.text


def test_successful_provisioning_job_persists_status_and_robot(provision_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake_backend, rest_module, tmp_path, _ProvisioningError = provision_client
    writes: list[dict[str, object]] = []

    def fake_write_robot_entry(result, role, robots_yaml_path, *, display_name=None, sdk_lib_path="sdk/zsl-1"):
        writes.append(
            {
                "robot_id": result.robot_id,
                "role": role,
                "display_name": display_name,
                "sdk_lib_path": sdk_lib_path,
                "robots_yaml_path": str(robots_yaml_path),
            }
        )
        return {
            "robot_id": result.robot_id,
            "display_name": display_name,
            "mac": result.quadruped_mac,
            "quadruped_ip": result.quadruped_ip,
            "role": role,
            "enabled": True,
        }

    monkeypatch.setattr(rest_module.provision_backend, "write_robot_entry", fake_write_robot_entry)

    response = client.post(
        "/provision/start",
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
        json={
            "quadruped_ap_ssid": "D1-Ultra:aa:bb:cc:dd:ee",
            "target_wifi_ssid": "FACTORY_WIFI",
            "target_wifi_password": "secret",
            "role": "logistics",
            "robot_id": "logistics_01",
            "display_name": "Logistics Robot 1",
            "pc_wifi_iface": "wlan0",
        },
    )

    assert response.status_code == 200
    job_id = response.json()["job_id"]
    status_body = _poll_job_status(client, job_id)

    assert status_body == {
        "job_id": job_id,
        "status": "succeeded",
        "message": "Provisioning complete",
        "robot_id": "logistics_01",
        "quadruped_mac": "aa:bb:cc:dd:ee:01",
        "quadruped_ip": "192.168.1.50",
    }
    assert writes == [
        {
            "robot_id": "logistics_01",
            "role": "logistics",
            "display_name": "Logistics Robot 1",
            "sdk_lib_path": "sdk/zsl-1",
            "robots_yaml_path": str(tmp_path / "robots.yaml"),
        }
    ]
    assert fake_backend.last_request is not None
    assert fake_backend.last_request.target_wifi_password == "secret"


def test_failed_provisioning_job_reports_safe_failure_and_does_not_write_yaml(
    provision_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, fake_backend, rest_module, _tmp_path, _ProvisioningError = provision_client
    fake_backend.provision_result = SimpleNamespace(
        success=False,
        message="Provision failed for secret",
        robot_id=None,
        quadruped_mac=None,
        quadruped_ip=None,
    )
    wrote = {"value": False}

    def fake_write_robot_entry(*_args, **_kwargs):
        wrote["value"] = True
        raise AssertionError("write_robot_entry should not be called for failed provisioning")

    monkeypatch.setattr(rest_module.provision_backend, "write_robot_entry", fake_write_robot_entry)

    response = client.post(
        "/provision/start",
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
        json={
            "quadruped_ap_ssid": "D1-Ultra:aa:bb:cc:dd:ee",
            "target_wifi_ssid": "FACTORY_WIFI",
            "target_wifi_password": "secret",
            "role": "logistics",
            "pc_wifi_iface": "wlan0",
        },
    )

    job_id = response.json()["job_id"]
    status_body = _poll_job_status(client, job_id)

    assert status_body["status"] == "failed"
    assert status_body["robot_id"] is None
    assert "secret" not in (status_body["message"] or "")
    assert wrote["value"] is False


def test_get_provision_status_unknown_job_returns_404(provision_client) -> None:
    client, *_ = provision_client

    response = client.get("/provision/status/missing-job", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 404


def test_get_provision_robots_lists_entries_from_yaml_helper(provision_client) -> None:
    client, fake_backend, *_ = provision_client
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

    response = client.get("/provision/robots", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert response.json() == [
        {
            "robot_id": "logistics_01",
            "display_name": "Logistics Robot 1",
            "mac": "aa:bb:cc:dd:ee:01",
            "quadruped_ip": "192.168.1.50",
            "role": "logistics",
            "enabled": True,
        }
    ]


def test_delete_provision_robot_removes_entry(provision_client) -> None:
    client, fake_backend, *_ = provision_client

    response = client.delete(
        "/provision/robots/logistics_01",
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code == 200
    assert response.json() == {
        "robot_id": "logistics_01",
        "display_name": "Logistics Robot 1",
        "mac": "aa:bb:cc:dd:ee:01",
        "quadruped_ip": "192.168.1.50",
        "role": "logistics",
        "enabled": True,
    }


def test_password_never_appears_in_provision_api_responses(provision_client) -> None:
    client, fake_backend, *_ = provision_client
    fake_backend.provision_result = SimpleNamespace(
        success=False,
        message="secret should never be exposed",
        robot_id=None,
        quadruped_mac=None,
        quadruped_ip=None,
    )

    start_response = client.post(
        "/provision/start",
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
        json={
            "quadruped_ap_ssid": "D1-Ultra:aa:bb:cc:dd:ee",
            "target_wifi_ssid": "FACTORY_WIFI",
            "target_wifi_password": "secret",
            "role": "logistics",
            "pc_wifi_iface": "wlan0",
        },
    )
    job_id = start_response.json()["job_id"]
    status_response = client.get(
        f"/provision/status/{job_id}",
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert "secret" not in start_response.text
    assert "secret" not in status_response.text
