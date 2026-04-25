from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TEST_OPERATOR_TOKEN = "test-operator-token"
TEST_QA_TOKEN = "test-qa-token"
TEST_SUPERVISOR_TOKEN = "test-supervisor-token"


class LifecycleStub:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    async def start(self) -> None:
        self.calls.append(f"start:{self.name}")

    async def stop(self) -> None:
        self.calls.append(f"stop:{self.name}")


class DatabaseStub:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def initialize(self) -> None:
        self.calls.append("database.initialize")

    async def close(self) -> None:
        self.calls.append("database.close")


class RouteStoreStub:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def load(self) -> None:
        self.calls.append("route_store.load")


class FakeDatabase:
    def __init__(self) -> None:
        self.initialized = False
        self.events: list[dict[str, object]] = []

    async def initialize(self) -> None:
        self.initialized = True

    async def log_event(self, **kwargs):
        self.events.append(kwargs)
        return kwargs.get("event_id", "event-id")


class FakeBroker:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def broadcast(self, message, **kwargs) -> None:
        self.messages.append(message)


def _create_test_app(monkeypatch: pytest.MonkeyPatch):
    from core.config import AppConfig, AuthSection
    from core.event_bus import EventBus

    import api.alerts as alerts_module
    import api.auth as auth_module
    import api.rest as rest_module
    import api.ws_broker as ws_module

    config = AppConfig(
        auth=AuthSection(
            operator_token=TEST_OPERATOR_TOKEN,
            qa_token=TEST_QA_TOKEN,
            supervisor_token=TEST_SUPERVISOR_TOKEN,
        )
    )
    event_bus = EventBus()
    ws_broker = ws_module.WebSocketBroker(event_bus=event_bus)
    alert_manager = alerts_module.AlertManager(database=FakeDatabase(), ws_broker=FakeBroker(), email_enabled=False)
    alert_manager._event_bus = event_bus

    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    monkeypatch.setattr(ws_module, "get_ws_broker", lambda: ws_broker)
    monkeypatch.setattr(rest_module, "get_ws_broker", lambda: ws_broker)
    monkeypatch.setattr(rest_module, "get_alert_manager", lambda: alert_manager)
    monkeypatch.setattr(rest_module, "startup_system", _noop_async)
    monkeypatch.setattr(rest_module, "shutdown_system", _noop_async)

    return rest_module.create_app()


async def _noop_async() -> None:
    return None


@pytest.mark.asyncio
async def test_full_startup_and_shutdown_without_hardware(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    sys.modules.pop("main", None)
    main_module = importlib.import_module("main")
    base_startup_module = main_module.base_startup

    monkeypatch.setattr(base_startup_module, "setup_logging", lambda: calls.append("setup_logging"))
    monkeypatch.setattr(
        base_startup_module,
        "get_config",
        lambda: SimpleNamespace(quadruped=SimpleNamespace(auto_stand_on_startup=False)),
    )
    monkeypatch.setattr(base_startup_module, "get_database", lambda: DatabaseStub(calls))
    monkeypatch.setattr(base_startup_module, "get_route_store", lambda: RouteStoreStub(calls))
    monkeypatch.setattr(base_startup_module, "get_event_bus", lambda: LifecycleStub("event_bus", calls))
    monkeypatch.setattr(base_startup_module, "get_heartbeat_controller", lambda: LifecycleStub("heartbeat", calls))
    monkeypatch.setattr(base_startup_module, "get_state_monitor", lambda: LifecycleStub("state_monitor", calls))
    monkeypatch.setattr(base_startup_module, "get_obstacle_detector", lambda: LifecycleStub("obstacle", calls))
    monkeypatch.setattr(main_module, "get_dispatcher", lambda: LifecycleStub("dispatcher", calls))
    monkeypatch.setattr(main_module, "get_battery_manager", lambda: LifecycleStub("battery", calls))
    monkeypatch.setattr(main_module, "get_watchdog", lambda: LifecycleStub("watchdog", calls))
    monkeypatch.setattr(
        base_startup_module,
        "get_sdk_adapter",
        lambda: SimpleNamespace(connect=_record_async(calls, "sdk.connect"), stand_up=_record_true_async(calls, "sdk.stand_up")),
    )

    await main_module.startup_system()
    await main_module.shutdown_system()

    assert "setup_logging" in calls
    assert "database.initialize" in calls
    assert "route_store.load" in calls
    assert "start:event_bus" in calls
    assert "stop:event_bus" in calls
    assert calls[-1] == "database.close"


def _record_async(calls: list[str], name: str):
    async def _inner():
        calls.append(name)

    return _inner


def _record_true_async(calls: list[str], name: str):
    async def _inner():
        calls.append(name)
        return True

    return _inner


def test_rest_health_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _create_test_app(monkeypatch)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_static_ui_files_served(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _create_test_app(monkeypatch)

    with TestClient(app) as client:
        operator_response = client.get("/ui/operator.html")
        supervisor_response = client.get("/ui/supervisor.html")
        kiosk_response = client.get("/ui/kiosk.html")

    assert operator_response.status_code == 200
    assert supervisor_response.status_code == 200
    assert kiosk_response.status_code == 200


def test_websocket_route_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _create_test_app(monkeypatch)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?token={TEST_SUPERVISOR_TOKEN}") as websocket:
            websocket.send_text("ping")


def test_phase1_safe_stub_modules_import() -> None:
    from hardware.gpio_relay import get_gpio_relay
    from hardware.mes_bridge import get_mes_bridge
    from hardware.qr_anchor import get_qr_anchor_reader
    from hardware.video_reader import get_video_reader
    from navigation.obstacle import get_obstacle_detector
    from navigation.slam import get_slam_provider

    assert get_gpio_relay() is not None
    assert get_video_reader() is not None
    assert get_qr_anchor_reader() is not None
    assert get_mes_bridge() is not None
    assert get_obstacle_detector() is not None
    assert get_slam_provider() is not None


def test_no_hardware_dependencies_required() -> None:
    for module_name in ("cv2", "pyzbar", "RPi", "RPi.GPIO", "GPIO"):
        sys.modules.pop(module_name, None)

    gpio_relay = importlib.import_module("hardware.gpio_relay")
    video_reader = importlib.import_module("hardware.video_reader")
    qr_anchor = importlib.import_module("hardware.qr_anchor")
    mes_bridge = importlib.import_module("hardware.mes_bridge")

    assert gpio_relay.get_gpio_relay() is not None
    assert video_reader.get_video_reader() is not None
    assert qr_anchor.get_qr_anchor_reader() is not None
    assert mes_bridge.get_mes_bridge() is not None
    for module_name in ("cv2", "pyzbar", "RPi", "RPi.GPIO", "GPIO"):
        assert module_name not in sys.modules


@pytest.mark.asyncio
async def test_database_initialize_and_queue_task_flow(tmp_path: Path) -> None:
    from core.database import Database
    from tasks.queue import TaskQueue

    database = Database(db_path=tmp_path / "phase1-integration.db")
    await database.initialize()
    queue = TaskQueue(database=database)

    try:
        task = await queue.submit_task(
            station_id="A",
            destination_id="QA",
            batch_id="BATCH-001",
            priority=1,
            notes="integration smoke test",
        )
        await queue.mark_dispatched(task.id)
        await queue.mark_awaiting_load(task.id)
        await queue.mark_in_transit(task.id)
        await queue.mark_awaiting_unload(task.id)
        completed = await queue.mark_completed(task.id)
        persisted = await queue.get_task(task.id)
    finally:
        await database.close()

    assert completed.status == "completed"
    assert persisted.status == "completed"
    assert persisted.completed_at is not None


@pytest.mark.asyncio
async def test_event_bus_to_alert_manager_flow() -> None:
    from api.alerts import AlertManager
    from core.event_bus import EventBus, EventName

    event_bus = EventBus()
    database = FakeDatabase()
    broker = FakeBroker()
    alert_manager = AlertManager(database=database, ws_broker=broker, email_enabled=False)
    alert_manager._event_bus = event_bus

    await event_bus.start()
    await alert_manager.start()
    try:
        await event_bus.publish(
            EventName.SYSTEM_ALERT,
            payload={
                "severity": "critical",
                "reason": "integration_smoke",
                "message": "Integration smoke alert",
                "module": "tests.integration",
                "station_id": "A",
            },
            source="tests.integration",
            task_id="task-integration",
        )
        await event_bus.wait_until_idle()
        last_alert = await alert_manager.get_last_alert()
    finally:
        await alert_manager.stop()
        await event_bus.stop()

    assert last_alert is not None
    assert last_alert.reason == "integration_smoke"
    assert database.initialized is True
    assert database.events[0]["event_name"] == "system.alert"
    assert database.events[0]["task_id"] == "task-integration"
    assert broker.messages == [{"type": "alert", "alert": last_alert.to_dict()}]
