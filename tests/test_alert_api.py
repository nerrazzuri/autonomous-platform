from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TEST_SUPERVISOR_TOKEN = "test-supervisor-token"


def build_auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeAsyncService:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


@pytest.fixture
def alert_api_client(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from core.config import AppConfig, AuthSection
    from shared.observability.alerts import AlertRouter

    import api.auth as auth_module
    import api.rest as rest_module

    config = AppConfig(
        auth=AuthSection(
            operator_token="test-operator-token",
            qa_token="test-qa-token",
            supervisor_token=TEST_SUPERVISOR_TOKEN,
        )
    )
    router = AlertRouter(max_alerts=20)

    async def _noop_async() -> None:
        return None

    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    monkeypatch.setattr(rest_module, "startup_system", _noop_async)
    monkeypatch.setattr(rest_module, "shutdown_system", _noop_async)
    monkeypatch.setattr(rest_module, "get_ws_broker", lambda: FakeAsyncService())
    monkeypatch.setattr(rest_module, "get_alert_manager", lambda: FakeAsyncService())
    monkeypatch.setattr(rest_module, "get_alert_router", lambda: router)

    client = TestClient(rest_module.create_app())
    return client, router


def _emit_sample_alerts(router) -> list[object]:
    from shared.observability.alerts import Alert

    first = router.emit(
        Alert(
            alert_id="alert-1",
            timestamp="2026-04-28T09:00:00Z",
            severity="warning",
            source="watchdog",
            event_type="watchdog_stale_robot",
            message="Robot telemetry is stale",
            robot_id="robot-01",
            metadata={"password": "secret-value", "component": "watchdog"},
        )
    )
    second = router.emit(
        Alert(
            alert_id="alert-2",
            timestamp="2026-04-28T09:05:00Z",
            severity="critical",
            source="battery",
            event_type="battery_critical",
            message="Battery level is critical",
            robot_id="robot-02",
            metadata={"battery_pct": 8},
        )
    )
    return [first, second]


def test_get_alerts_lists_alerts(alert_api_client) -> None:
    client, router = alert_api_client
    _emit_sample_alerts(router)

    response = client.get("/alerts", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    body = response.json()
    assert [item["alert_id"] for item in body] == ["alert-2", "alert-1"]


def test_get_alert_returns_single_alert(alert_api_client) -> None:
    client, router = alert_api_client
    _emit_sample_alerts(router)

    response = client.get("/alerts/alert-1", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert response.json()["event_type"] == "watchdog_stale_robot"


def test_get_unknown_alert_returns_404(alert_api_client) -> None:
    client, _router = alert_api_client

    response = client.get("/alerts/missing", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 404


def test_acknowledge_alert_marks_alert(alert_api_client) -> None:
    client, router = alert_api_client
    _emit_sample_alerts(router)

    response = client.post(
        "/alerts/alert-1/acknowledge",
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
        json={"actor_id": "operator-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["acknowledged"] is True
    assert body["acknowledged_by"] == "operator-1"


def test_alert_filters_work(alert_api_client) -> None:
    client, router = alert_api_client
    _emit_sample_alerts(router)
    router.acknowledge("alert-1", actor_id="operator-2")

    response = client.get(
        "/alerts?severity=warning&robot_id=robot-01&acknowledged=true",
        headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["alert_id"] == "alert-1"


def test_secret_metadata_is_redacted_in_api_response(alert_api_client) -> None:
    client, router = alert_api_client
    _emit_sample_alerts(router)

    response = client.get("/alerts/alert-1", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))

    assert response.status_code == 200
    assert response.json()["metadata"]["password"] == "***MASKED***"
