from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
TEST_SUPERVISOR_TOKEN = "test-supervisor-token"


@pytest.fixture(autouse=True)
def reset_status_state(monkeypatch: pytest.MonkeyPatch):
    from shared.diagnostics import reset_diagnostic_store
    import shared.observability.status as status_module

    reset_diagnostic_store()
    status_module.clear_status_providers()
    monkeypatch.setattr(status_module, "get_robot_registry", lambda: SimpleNamespace(all=lambda: []))
    monkeypatch.setattr(status_module, "get_alert_router", lambda: FakeAlertRouter([]))
    yield
    reset_diagnostic_store()
    status_module.clear_status_providers()


class FakeAlertRouter:
    def __init__(self, alerts: list[Any]):
        self._alerts = alerts

    def list_alerts(self, limit: int = 100):
        return self._alerts[:limit]


class FakeStateMonitor:
    async def get_current_state(self):
        return SimpleNamespace(
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            battery_pct=82,
            connection_ok=True,
        )


class FakeHeartbeat:
    def is_running(self) -> bool:
        return True

    def last_send_ok(self) -> bool:
        return True

    def last_error(self) -> str | None:
        return None


def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_SUPERVISOR_TOKEN}"}


@pytest.mark.asyncio
async def test_build_status_summary_without_providers_has_required_keys() -> None:
    from shared.observability.status import build_status_summary

    summary = await build_status_summary()

    assert set(summary) == {"status", "ts", "platform", "robots", "diagnostics", "alerts", "extensions"}
    assert summary["status"] == "ok"
    assert summary["robots"] == {}
    assert summary["extensions"] == {}


@pytest.mark.asyncio
async def test_diagnostic_counts_and_latest_error_are_redacted() -> None:
    from shared.diagnostics import DiagnosticSeverity, error_codes, get_diagnostic_store
    from shared.observability.status import build_status_summary

    get_diagnostic_store().create_event(
        severity=DiagnosticSeverity.ERROR,
        module="sdk_adapter",
        event="sdk.connect_failed",
        message="connect failed",
        error_code=error_codes.SDK_CONNECT_FAILED,
        details={"token": "secret-token", "host": "192.168.1.10"},
    )

    summary = await build_status_summary()

    assert summary["status"] == "degraded"
    assert summary["diagnostics"]["recent_count"] == 1
    assert summary["diagnostics"]["error_count"] == 1
    assert summary["diagnostics"]["critical_count"] == 0
    assert summary["diagnostics"]["latest_error"]["details"]["token"] == "[REDACTED]"
    assert summary["diagnostics"]["latest_error"]["details"]["host"] == "192.168.1.10"


@pytest.mark.asyncio
async def test_robot_status_uses_existing_registry_without_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    import shared.observability.status as status_module

    platform = SimpleNamespace(
        robot_id="robot_01",
        state_monitor=FakeStateMonitor(),
        heartbeat=FakeHeartbeat(),
    )
    monkeypatch.setattr(status_module, "get_robot_registry", lambda: SimpleNamespace(all=lambda: [platform]))

    summary = await status_module.build_status_summary()

    assert summary["robots"]["robot_01"]["status"] == "ok"
    assert summary["robots"]["robot_01"]["connected"] is True
    assert summary["robots"]["robot_01"]["battery"] == {"percent": 82, "state": "ok"}
    assert summary["robots"]["robot_01"]["last_telemetry_ts"] == "2026-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_status_provider_registration_adds_redacted_extension() -> None:
    from shared.observability.status import build_status_summary, get_registered_status_providers, register_status_provider

    register_status_provider(
        "custom_app",
        lambda: {
            "status": "ok",
            "api_token": "secret",
            "nested": {"password": "hidden", "visible": "value"},
        },
    )

    summary = await build_status_summary()

    assert "custom_app" in get_registered_status_providers()
    assert summary["extensions"]["custom_app"]["status"] == "ok"
    assert summary["extensions"]["custom_app"]["api_token"] == "[REDACTED]"
    assert summary["extensions"]["custom_app"]["nested"]["password"] == "[REDACTED]"
    assert summary["extensions"]["custom_app"]["nested"]["visible"] == "value"


@pytest.mark.asyncio
async def test_status_provider_failure_is_contained() -> None:
    from shared.observability.status import build_status_summary, register_status_provider

    def failing_provider():
        raise RuntimeError("boom")

    register_status_provider("failing", failing_provider)

    summary = await build_status_summary()

    assert summary["status"] == "degraded"
    assert summary["extensions"]["failing"]["status"] == "error"
    assert summary["extensions"]["failing"]["error"] == "provider_failed"


def test_provider_registry_replaces_duplicate_names_and_validates_names() -> None:
    from shared.observability.status import (
        get_registered_status_providers,
        register_status_provider,
        unregister_status_provider,
    )

    first = lambda: {"status": "first"}
    second = lambda: {"status": "second"}

    register_status_provider("module.name", first)
    register_status_provider("module.name", second)

    assert get_registered_status_providers()["module.name"] is second

    unregister_status_provider("module.name")
    assert "module.name" not in get_registered_status_providers()

    with pytest.raises(ValueError):
        register_status_provider("../bad", first)


@pytest.mark.asyncio
async def test_app_status_providers_register_app_owned_extensions() -> None:
    from apps.logistics.observability.status import register_logistics_status_provider
    from apps.patrol.observability.status import register_patrol_status_provider
    from shared.observability.status import build_status_summary

    register_logistics_status_provider()
    register_patrol_status_provider()

    summary = await build_status_summary()

    assert summary["extensions"]["logistics"]["status"] == "unknown"
    assert summary["extensions"]["patrol"]["status"] == "unknown"


def test_shared_status_module_does_not_import_apps_or_encode_app_workflows() -> None:
    content = (ROOT / "shared/observability/status.py").read_text(encoding="utf-8")

    assert "from apps" not in content
    assert "import apps" not in content
    for forbidden in ("LINE_A", "LINE_B", "LINE_C", "Sumitomo", "load", "unload", "patrol cycle", "patrol waypoint"):
        assert forbidden not in content


def test_logistics_status_endpoint_returns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient
    from shared.core.config import AppConfig, AuthSection
    import shared.api.auth as auth_module
    import apps.logistics.api.rest as rest_module

    config = AppConfig(auth=AuthSection(supervisor_token=TEST_SUPERVISOR_TOKEN))

    async def fake_summary():
        return {
            "status": "ok",
            "ts": "2026-01-01T00:00:00+00:00",
            "platform": {"uptime_seconds": 1.0, "version": None, "app": "test"},
            "robots": {},
            "diagnostics": {"recent_count": 0, "error_count": 0, "critical_count": 0, "latest_error": None},
            "alerts": {"active_count": 0, "latest": None},
            "extensions": {},
        }

    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    monkeypatch.setattr(rest_module, "startup_system", _noop_async)
    monkeypatch.setattr(rest_module, "shutdown_system", _noop_async)
    monkeypatch.setattr(rest_module, "build_status_summary", fake_summary)

    client = TestClient(rest_module.create_app())

    assert client.get("/status/summary").status_code == 401
    response = client.get("/status/summary", headers=_auth_header())
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_patrol_status_endpoint_returns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient
    from shared.core.config import AppConfig, AuthSection
    import shared.api.auth as auth_module
    import apps.patrol.api.rest as rest_module

    config = AppConfig(auth=AuthSection(supervisor_token=TEST_SUPERVISOR_TOKEN))

    async def fake_summary():
        return {
            "status": "ok",
            "ts": "2026-01-01T00:00:00+00:00",
            "platform": {"uptime_seconds": 1.0, "version": None, "app": "test"},
            "robots": {},
            "diagnostics": {"recent_count": 0, "error_count": 0, "critical_count": 0, "latest_error": None},
            "alerts": {"active_count": 0, "latest": None},
            "extensions": {},
        }

    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    monkeypatch.setattr(rest_module, "startup_system", _noop_async)
    monkeypatch.setattr(rest_module, "shutdown_system", _noop_async)
    monkeypatch.setattr(rest_module, "build_status_summary", fake_summary)

    client = TestClient(rest_module.create_app())

    assert client.get("/status/summary").status_code == 401
    response = client.get("/status/summary", headers=_auth_header())
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def _noop_async() -> None:
    return None
