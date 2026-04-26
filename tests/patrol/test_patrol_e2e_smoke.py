from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_manual_patrol_smoke_script_exists_and_is_executable() -> None:
    script_path = ROOT / "apps" / "patrol" / "scripts" / "manual_patrol_smoke.sh"

    assert script_path.exists()
    assert os.access(script_path, os.X_OK)

    mode = script_path.stat().st_mode
    assert mode & stat.S_IXUSR


def test_manual_patrol_smoke_script_contains_required_endpoints_and_env_vars() -> None:
    script_path = ROOT / "apps" / "patrol" / "scripts" / "manual_patrol_smoke.sh"
    content = script_path.read_text(encoding="utf-8")

    assert "/health" in content
    assert "/patrol/status" in content
    assert "/patrol/routes" in content
    assert "/patrol/zones" in content
    assert "/patrol/trigger" in content
    assert "/patrol/cycles" in content
    assert "/patrol/anomalies" in content
    assert "/patrol/suspend" in content
    assert "/patrol/resume" in content
    assert "/estop" in content
    assert "/estop/release" in content

    assert "BASE_URL" in content
    assert "SUPERVISOR_TOKEN" in content
    assert "ROUTE_ID" in content


def test_manual_patrol_smoke_script_does_not_require_jq() -> None:
    script_path = ROOT / "apps" / "patrol" / "scripts" / "manual_patrol_smoke.sh"
    content = script_path.read_text(encoding="utf-8")

    assert "jq" not in content
    assert "python3" in content
    assert "curl" in content


def test_patrol_rest_app_serves_ui_assets_for_smoke() -> None:
    import apps.patrol.api.rest as rest_module

    async def noop() -> None:
        return None

    class FakeBroker:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    class FakeAlerts:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    rest_module.startup_system = noop
    rest_module.shutdown_system = noop
    rest_module.get_ws_broker = lambda: FakeBroker()
    rest_module.get_alert_manager = lambda: FakeAlerts()

    client = TestClient(rest_module.create_app())

    health_response = client.get("/health")
    supervisor_response = client.get("/ui/supervisor.html")
    anomaly_response = client.get("/ui/anomaly_log.html")
    floormap_response = client.get("/ui/floormap.js")

    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"
    assert supervisor_response.status_code == 200
    assert "Patrol Supervisor Dashboard" in supervisor_response.text
    assert anomaly_response.status_code == 200
    assert "Patrol Anomaly Log" in anomaly_response.text
    assert floormap_response.status_code == 200
    assert "window.PatrolFloorMap" in floormap_response.text
