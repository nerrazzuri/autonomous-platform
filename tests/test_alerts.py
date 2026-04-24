from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeDatabase:
    def __init__(self):
        self.initialized = False
        self.events: list[dict[str, object]] = []
        self.fail = False

    async def initialize(self):
        self.initialized = True

    async def log_event(self, **kwargs):
        if self.fail:
            raise RuntimeError("db failure")
        self.events.append(kwargs)
        return kwargs.get("event_id", "db-event-id")


class FakeBroker:
    def __init__(self):
        self.messages: list[dict[str, object]] = []
        self.fail = False

    async def broadcast(self, message, **kwargs):
        if self.fail:
            raise RuntimeError("broadcast failure")
        self.messages.append(message)


def make_alert_event(module, **payload_overrides):
    payload = {"reason": "telemetry_timeout", "severity": "critical", "station_id": "A"}
    payload.update(payload_overrides)
    return module.Event(
        name=module.EventName.SYSTEM_ALERT,
        payload=payload,
        event_id="alert-event-1",
        timestamp=datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc),
        source="quadruped.monitor",
        task_id="task-9",
    )


@pytest.fixture
def alerts_module():
    import api.alerts as module

    return module


def test_alert_message_from_event(alerts_module) -> None:
    event = make_alert_event(
        alerts_module,
        message="Telemetry stopped updating",
        active_task_id="task-9",
        station_id="A",
        battery_pct=12,
    )

    alert = alerts_module.AlertMessage.from_event(event)

    assert alert.alert_id == "alert-event-1"
    assert alert.severity == "critical"
    assert alert.reason == "telemetry_timeout"
    assert alert.module == "quadruped.monitor"
    assert alert.message == "Telemetry stopped updating"
    assert alert.active_task_id == "task-9"
    assert alert.metadata == {"station_id": "A", "battery_pct": 12}
    assert alert.to_dict()["timestamp"] == "2026-04-24T12:00:00+00:00"


def test_alert_message_defaults_missing_reason(alerts_module) -> None:
    event = alerts_module.Event(
        name=alerts_module.EventName.SYSTEM_ALERT,
        payload={},
        event_id="alert-event-2",
        timestamp=datetime(2026, 4, 24, 12, 5, tzinfo=timezone.utc),
        source=None,
    )

    alert = alerts_module.AlertMessage.from_event(event)

    assert alert.reason == "unspecified"
    assert alert.module == "unknown"
    assert alert.message == "WARNING: unspecified"


def test_alert_message_rejects_invalid_severity(alerts_module) -> None:
    event = make_alert_event(alerts_module, severity="emergency")

    with pytest.raises(alerts_module.AlertManagerError, match="severity"):
        alerts_module.AlertMessage.from_event(event)


@pytest.mark.asyncio
async def test_start_and_stop_are_idempotent(alerts_module) -> None:
    from core.event_bus import EventBus

    event_bus = EventBus()
    manager = alerts_module.AlertManager(FakeDatabase(), FakeBroker(), email_enabled=False)
    manager._event_bus = event_bus

    await manager.start()
    await manager.start()
    assert manager.is_running() is True
    assert event_bus.subscriber_count(alerts_module.EventName.SYSTEM_ALERT) == 1

    await manager.stop()
    await manager.stop()
    assert manager.is_running() is False
    assert event_bus.subscriber_count(alerts_module.EventName.SYSTEM_ALERT) == 0


@pytest.mark.asyncio
async def test_handle_alert_persists_to_database(alerts_module) -> None:
    database = FakeDatabase()
    broker = FakeBroker()
    manager = alerts_module.AlertManager(database=database, ws_broker=broker, email_enabled=False)
    event = make_alert_event(alerts_module, message="Telemetry stopped updating")

    alert = await manager.handle_alert_event(event)

    assert alert.reason == "telemetry_timeout"
    assert database.initialized is True
    assert database.events == [
        {
            "event_name": "system.alert",
            "payload": alert.to_dict(),
            "source": "quadruped.monitor",
            "task_id": "task-9",
            "event_id": "alert-event-1",
        }
    ]
    assert await manager.get_last_alert() == alert


@pytest.mark.asyncio
async def test_database_failure_does_not_block_broadcast(alerts_module) -> None:
    database = FakeDatabase()
    database.fail = True
    broker = FakeBroker()
    manager = alerts_module.AlertManager(database=database, ws_broker=broker, email_enabled=False)

    alert = await manager.handle_alert_event(make_alert_event(alerts_module))

    assert alert.alert_id == "alert-event-1"
    assert broker.messages == [{"type": "alert", "alert": alert.to_dict()}]
    assert manager.last_error() == "db failure"


@pytest.mark.asyncio
async def test_broadcast_failure_does_not_block_persistence(alerts_module) -> None:
    database = FakeDatabase()
    broker = FakeBroker()
    broker.fail = True
    manager = alerts_module.AlertManager(database=database, ws_broker=broker, email_enabled=False)

    alert = await manager.handle_alert_event(make_alert_event(alerts_module))

    assert database.events[0]["payload"] == alert.to_dict()
    assert manager.last_error() == "broadcast failure"


@pytest.mark.asyncio
async def test_email_disabled_does_not_send(alerts_module, monkeypatch: pytest.MonkeyPatch) -> None:
    smtp_calls: list[tuple[str, int]] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int):
            smtp_calls.append((host, port))

    monkeypatch.setattr(alerts_module.smtplib, "SMTP", FakeSMTP)

    manager = alerts_module.AlertManager(database=FakeDatabase(), ws_broker=FakeBroker(), email_enabled=False)
    await manager.handle_alert_event(make_alert_event(alerts_module))

    assert smtp_calls == []


@pytest.mark.asyncio
async def test_email_enabled_sends_email_with_complete_config(
    alerts_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: dict[str, object] = {}

    class FakeSMTP:
        def __init__(self, host: str, port: int):
            sent["host"] = host
            sent["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def login(self, username: str, password: str):
            sent["login"] = (username, password)

        def send_message(self, message):
            sent["subject"] = message["Subject"]
            sent["to"] = message["To"]
            sent["body"] = message.get_content()

    monkeypatch.setattr(
        alerts_module,
        "get_config",
        lambda: SimpleNamespace(
            alerts=SimpleNamespace(
                smtp_host="smtp.example.com",
                smtp_port=2525,
                smtp_username="robot",
                smtp_password="secret-password",
                supervisor_email="supervisor@example.com",
                email_enabled=True,
            )
        ),
    )
    monkeypatch.setattr(alerts_module.smtplib, "SMTP", FakeSMTP)

    manager = alerts_module.AlertManager(database=FakeDatabase(), ws_broker=FakeBroker(), email_enabled=True)
    await manager.handle_alert_event(make_alert_event(alerts_module))

    assert sent["host"] == "smtp.example.com"
    assert sent["port"] == 2525
    assert sent["login"] == ("robot", "secret-password")
    assert sent["to"] == "supervisor@example.com"
    assert sent["subject"] == "[Sumitomo Quadruped] CRITICAL alert: telemetry_timeout"
    assert "severity: critical" in sent["body"]
    assert '"station_id": "A"' in sent["body"]


@pytest.mark.asyncio
async def test_email_incomplete_config_skips_safely(alerts_module, monkeypatch: pytest.MonkeyPatch) -> None:
    smtp_calls: list[tuple[str, int]] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int):
            smtp_calls.append((host, port))

    monkeypatch.setattr(
        alerts_module,
        "get_config",
        lambda: SimpleNamespace(
            alerts=SimpleNamespace(
                smtp_host=None,
                smtp_port=2525,
                smtp_username=None,
                smtp_password=None,
                supervisor_email=None,
                email_enabled=True,
            )
        ),
    )
    monkeypatch.setattr(alerts_module.smtplib, "SMTP", FakeSMTP)

    manager = alerts_module.AlertManager(database=FakeDatabase(), ws_broker=FakeBroker(), email_enabled=True)
    alert = await manager.handle_alert_event(make_alert_event(alerts_module))

    assert alert.reason == "telemetry_timeout"
    assert smtp_calls == []


@pytest.mark.asyncio
async def test_invalid_event_type_raises(alerts_module) -> None:
    manager = alerts_module.AlertManager(database=FakeDatabase(), ws_broker=FakeBroker(), email_enabled=False)
    event = alerts_module.Event(name=alerts_module.EventName.BATTERY_WARN, payload={"reason": "low_battery"})

    with pytest.raises(alerts_module.AlertManagerError, match="SYSTEM_ALERT"):
        await manager.handle_alert_event(event)


def test_global_get_alert_manager_returns_manager(alerts_module) -> None:
    assert alerts_module.get_alert_manager() is alerts_module.alert_manager


def test_rest_lifespan_starts_and_stops_alert_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    import api.rest as rest_module

    calls: list[str] = []

    class FakeManager:
        async def start(self) -> None:
            calls.append("alert-start")

        async def stop(self) -> None:
            calls.append("alert-stop")

    class FakeBrokerManager:
        async def start(self) -> None:
            calls.append("ws-start")

        async def stop(self) -> None:
            calls.append("ws-stop")

    monkeypatch.setattr(rest_module, "get_alert_manager", lambda: FakeManager())
    monkeypatch.setattr(rest_module, "get_ws_broker", lambda: FakeBrokerManager())

    app = rest_module.create_app()

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200

    assert calls == ["ws-start", "alert-start", "alert-stop", "ws-stop"]

