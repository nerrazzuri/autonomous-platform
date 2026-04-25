from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_rest_lifespan_starts_full_runtime_and_shared_api_services(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.logistics.api.rest as rest_module

    calls: list[str] = []

    class FakeBrokerManager:
        def __init__(self) -> None:
            self.start_calls = 0
            self.stop_calls = 0

        async def start(self) -> None:
            self.start_calls += 1
            calls.append("ws-start")

        async def stop(self) -> None:
            self.stop_calls += 1
            calls.append("ws-stop")

    class FakeAlertManager:
        def __init__(self) -> None:
            self.start_calls = 0
            self.stop_calls = 0

        async def start(self) -> None:
            self.start_calls += 1
            calls.append("alert-start")

        async def stop(self) -> None:
            self.stop_calls += 1
            calls.append("alert-stop")

    broker = FakeBrokerManager()
    alerts = FakeAlertManager()

    async def startup_runtime() -> None:
        calls.append("runtime-start")

    async def shutdown_runtime() -> None:
        calls.append("runtime-stop")

    monkeypatch.setattr(rest_module, "startup_system", startup_runtime)
    monkeypatch.setattr(rest_module, "shutdown_system", shutdown_runtime)
    monkeypatch.setattr(rest_module, "get_ws_broker", lambda: broker)
    monkeypatch.setattr(rest_module, "get_alert_manager", lambda: alerts)

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
    assert broker.start_calls == 1
    assert broker.stop_calls == 1
    assert alerts.start_calls == 1
    assert alerts.stop_calls == 1
