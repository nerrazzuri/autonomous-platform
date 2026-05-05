from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_provision_html_exists_and_references_required_endpoints() -> None:
    provision_html = ROOT / "apps" / "logistics" / "ui" / "provision.html"

    assert provision_html.exists()

    content = provision_html.read_text(encoding="utf-8")

    assert "Robot Provisioning" in content
    assert "/provision/scan" in content
    assert "/provision/start" in content
    assert "/provision/status/" in content
    assert "/provision/robots" in content


def test_provision_ui_contains_required_form_fields_and_polling_logic() -> None:
    content = (ROOT / "apps" / "logistics" / "ui" / "provision.html").read_text(encoding="utf-8")

    assert 'id="quadruped_ap_ssid"' in content
    assert 'id="target_wifi_ssid"' in content
    assert 'id="target_wifi_password"' in content
    assert 'id="role"' in content
    assert 'id="robot_id"' in content
    assert 'id="display_name"' in content
    assert "pollJobStatus" in content
    assert "setInterval" in content
    assert "clearStatusPolling" in content


def test_provision_ui_has_password_safety_and_delete_logic() -> None:
    content = (ROOT / "apps" / "logistics" / "ui" / "provision.html").read_text(encoding="utf-8")

    assert 'id="target_wifi_password"' in content
    assert 'type="password"' in content
    assert "targetWifiPassword.value = \"\"" in content
    assert "sshPassword.value = \"\"" in content
    assert "removeProvisionedRobot" in content
    assert "DELETE" in content
    assert "console.log" not in content
    assert "console.error" not in content


def test_supervisor_html_links_to_provision_page() -> None:
    content = (ROOT / "apps" / "logistics" / "ui" / "supervisor.html").read_text(encoding="utf-8")

    assert "/ui/provision.html" in content
    assert "Provision Robots" in content
    assert "updateProvisionLink" in content


def test_rest_app_serves_provision_html() -> None:
    import api.rest as rest_module

    async def noop() -> None:
        return None

    class FakeService:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    rest_module.startup_system = noop
    rest_module.shutdown_system = noop
    rest_module.get_ws_broker = lambda: FakeService()
    rest_module.get_alert_manager = lambda: FakeService()
    client = TestClient(rest_module.create_app())

    response = client.get("/ui/provision.html")

    assert response.status_code == 200
    assert "Robot Provisioning" in response.text
