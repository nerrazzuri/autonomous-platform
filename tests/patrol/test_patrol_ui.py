from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_supervisor_html_exists_with_required_markers() -> None:
    supervisor_html = ROOT / "apps" / "patrol" / "ui" / "supervisor.html"

    assert supervisor_html.exists()

    content = supervisor_html.read_text(encoding="utf-8")

    assert "Patrol Supervisor Dashboard" in content
    assert "/ws" in content
    assert "/robots" in content
    assert "/robots/" in content
    assert "/patrol/status" in content
    assert "/patrol/cycles" in content
    assert "/patrol/anomalies" in content
    assert "/patrol/trigger" in content
    assert "/patrol/suspend" in content
    assert "/patrol/resume" in content
    assert "/estop" in content
    assert "/estop/release" in content
    assert "/robots/\" + encodeURIComponent(robotId) + \"/estop" in content
    assert "/robots/\" + encodeURIComponent(robotId) + \"/estop/release" in content
    assert "fleet-grid" in content
    assert "selected-robot-details" in content
    assert "fleet-map-layer" in content
    assert "floormap.js" in content


def test_anomaly_log_html_exists_with_required_markers() -> None:
    anomaly_html = ROOT / "apps" / "patrol" / "ui" / "anomaly_log.html"

    assert anomaly_html.exists()

    content = anomaly_html.read_text(encoding="utf-8")

    assert "Patrol Anomaly Log" in content
    assert "/patrol/anomalies" in content
    assert "/patrol/zones" in content
    assert "/patrol/anomalies/${encodeURIComponent(anomalyId)}/resolve" in content
    assert "Print Report" in content


def test_floormap_js_exists_with_required_api() -> None:
    floormap_js = ROOT / "apps" / "patrol" / "ui" / "floormap.js"

    assert floormap_js.exists()

    content = floormap_js.read_text(encoding="utf-8")

    assert "window.PatrolFloorMap" in content
    assert "create" in content
    assert "setRoutes" in content
    assert "setZones" in content
    assert "updateRobotPosition" in content
    assert "markAnomaly" in content
    assert "clearAnomaly" in content
    assert "destroy" in content


def test_rest_app_serves_patrol_ui_assets() -> None:
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

    supervisor_response = client.get("/ui/supervisor.html")
    anomaly_response = client.get("/ui/anomaly_log.html")
    floormap_response = client.get("/ui/floormap.js")

    assert supervisor_response.status_code == 200
    assert "Patrol Supervisor Dashboard" in supervisor_response.text
    assert anomaly_response.status_code == 200
    assert "Patrol Anomaly Log" in anomaly_response.text
    assert floormap_response.status_code == 200
    assert "window.PatrolFloorMap" in floormap_response.text
