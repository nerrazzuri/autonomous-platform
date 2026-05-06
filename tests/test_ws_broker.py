from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TEST_OPERATOR_TOKEN = "test-operator-token"
TEST_QA_TOKEN = "test-qa-token"
TEST_SUPERVISOR_TOKEN = "test-supervisor-token"


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.close_code: int | None = None
        self.sent_messages: list[dict[str, object]] = []
        self.fail_send = False

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.close_code = code

    async def send_json(self, message: dict[str, object]) -> None:
        if self.fail_send:
            raise RuntimeError("send failed")
        self.sent_messages.append(message)


@pytest.fixture
def ws_module(monkeypatch: pytest.MonkeyPatch):
    from core.config import AppConfig, AuthSection
    from core.event_bus import EventBus

    import api.auth as auth_module
    import api.ws_broker as module

    config = AppConfig(
        auth=AuthSection(
            operator_token=TEST_OPERATOR_TOKEN,
            qa_token=TEST_QA_TOKEN,
            supervisor_token=TEST_SUPERVISOR_TOKEN,
        )
    )
    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    module.clear_websocket_forwarding_events()
    module.register_platform_websocket_events()
    yield module, EventBus()
    module.clear_websocket_forwarding_events()
    module.register_platform_websocket_events()


@pytest.mark.asyncio
async def test_connect_rejects_missing_token(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    websocket = FakeWebSocket()

    with pytest.raises(module.WebSocketBrokerError):
        await broker.connect(websocket, token=None)

    assert websocket.accepted is False
    assert websocket.close_code == 1008
    assert broker.client_count() == 0


@pytest.mark.asyncio
async def test_connect_accepts_valid_operator_token(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    websocket = FakeWebSocket()

    client_id = await broker.connect(websocket, token=TEST_OPERATOR_TOKEN, station_id="A", robot_id="robot_01")

    assert isinstance(client_id, str)
    assert websocket.accepted is True
    assert broker.client_count() == 1
    assert broker._clients[client_id].role is module.Role.OPERATOR
    assert broker._clients[client_id].station_id == "A"
    assert broker._clients[client_id].robot_id == "robot_01"


@pytest.mark.asyncio
async def test_disconnect_removes_client(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    websocket = FakeWebSocket()

    client_id = await broker.connect(websocket, token=TEST_OPERATOR_TOKEN)
    await broker.disconnect(client_id)

    assert broker.client_count() == 0


@pytest.mark.asyncio
async def test_client_count(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)

    await broker.connect(FakeWebSocket(), token=TEST_OPERATOR_TOKEN, station_id="A")
    await broker.connect(FakeWebSocket(), token=TEST_QA_TOKEN)

    assert broker.client_count() == 2


@pytest.mark.asyncio
async def test_broadcast_sends_to_all_clients(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    operator_ws = FakeWebSocket()
    qa_ws = FakeWebSocket()

    await broker.connect(operator_ws, token=TEST_OPERATOR_TOKEN, station_id="A")
    await broker.connect(qa_ws, token=TEST_QA_TOKEN)

    await broker.broadcast({"type": "event", "payload": {"ok": True}})

    assert operator_ws.sent_messages == [{"type": "event", "payload": {"ok": True}}]
    assert qa_ws.sent_messages == [{"type": "event", "payload": {"ok": True}}]


@pytest.mark.asyncio
async def test_broadcast_filters_by_station_for_operator(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    station_a_ws = FakeWebSocket()
    station_b_ws = FakeWebSocket()

    await broker.connect(station_a_ws, token=TEST_OPERATOR_TOKEN, station_id="A")
    await broker.connect(station_b_ws, token=TEST_OPERATOR_TOKEN, station_id="B")

    await broker.broadcast({"type": "event", "payload": {"station_id": "A"}}, station_id="A")

    assert len(station_a_ws.sent_messages) == 1
    assert station_b_ws.sent_messages == []


@pytest.mark.asyncio
async def test_broadcast_supervisor_receives_station_events(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    operator_ws = FakeWebSocket()
    supervisor_ws = FakeWebSocket()

    await broker.connect(operator_ws, token=TEST_OPERATOR_TOKEN, station_id="B")
    await broker.connect(supervisor_ws, token=TEST_SUPERVISOR_TOKEN)

    await broker.broadcast({"type": "event", "payload": {"station_id": "A"}}, station_id="A")

    assert operator_ws.sent_messages == []
    assert supervisor_ws.sent_messages == [{"type": "event", "payload": {"station_id": "A"}}]


@pytest.mark.asyncio
async def test_handle_event_ignores_unlisted_event(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    websocket = FakeWebSocket()

    await broker.connect(websocket, token=TEST_OPERATOR_TOKEN, station_id="A")
    event = module.Event(name=module.EventName.NAVIGATION_STARTED, payload={"station_id": "A"})

    await broker.handle_event(event)

    assert websocket.sent_messages == []


@pytest.mark.asyncio
async def test_handle_event_broadcasts_listed_event(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    websocket = FakeWebSocket()
    module.register_websocket_forwarding_event(module.EventName.TASK_STATUS_CHANGED)

    await broker.connect(websocket, token=TEST_OPERATOR_TOKEN, station_id="A")
    event = module.Event(
        name=module.EventName.TASK_STATUS_CHANGED,
        payload={"station_id": "A", "status": "dispatched"},
        source="tests",
        task_id="task-1",
    )

    await broker.handle_event(event)

    assert websocket.sent_messages == [
        {
            "type": "event",
            "event_name": "task.status_changed",
            "event_id": event.event_id,
            "timestamp": event.timestamp.isoformat(),
            "source": "tests",
            "task_id": "task-1",
            "payload": {"station_id": "A", "status": "dispatched"},
        }
    ]

    module.unregister_websocket_forwarding_event(module.EventName.TASK_STATUS_CHANGED)


@pytest.mark.asyncio
async def test_robot_filtered_client_receives_matching_robot_event(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    websocket = FakeWebSocket()

    await broker.connect(websocket, token=TEST_OPERATOR_TOKEN, robot_id="robot_01")
    event = module.Event(
        name=module.EventName.QUADRUPED_TELEMETRY,
        payload={"robot_id": "robot_01", "battery_pct": 88},
    )

    await broker.handle_event(event)

    assert websocket.sent_messages[0]["payload"] == {"robot_id": "robot_01", "battery_pct": 88}


@pytest.mark.asyncio
async def test_robot_filtered_client_does_not_receive_other_robot_event(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    websocket = FakeWebSocket()

    await broker.connect(websocket, token=TEST_OPERATOR_TOKEN, robot_id="robot_01")
    event = module.Event(
        name=module.EventName.QUADRUPED_TELEMETRY,
        payload={"robot_id": "robot_02", "battery_pct": 88},
    )

    await broker.handle_event(event)

    assert websocket.sent_messages == []


@pytest.mark.asyncio
async def test_supervisor_without_robot_filter_receives_all_robot_events(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    websocket = FakeWebSocket()

    await broker.connect(websocket, token=TEST_SUPERVISOR_TOKEN)
    await broker.handle_event(module.Event(name=module.EventName.QUADRUPED_TELEMETRY, payload={"robot_id": "robot_01"}))
    await broker.handle_event(module.Event(name=module.EventName.QUADRUPED_TELEMETRY, payload={"robot_id": "robot_02"}))

    assert [message["payload"]["robot_id"] for message in websocket.sent_messages] == ["robot_01", "robot_02"]


@pytest.mark.asyncio
async def test_robot_filtered_client_receives_global_event_without_robot_id(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    websocket = FakeWebSocket()

    await broker.connect(websocket, token=TEST_OPERATOR_TOKEN, robot_id="robot_01")
    event = module.Event(
        name=module.EventName.SYSTEM_ALERT,
        payload={"message": "system online"},
    )

    await broker.handle_event(event)

    assert websocket.sent_messages[0]["payload"] == {"message": "system online"}


@pytest.mark.asyncio
async def test_handle_event_with_none_or_non_dict_payload_does_not_crash(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    websocket = FakeWebSocket()

    await broker.connect(websocket, token=TEST_OPERATOR_TOKEN, robot_id="robot_01")

    await broker.handle_event(module.Event(name=module.EventName.SYSTEM_ALERT, payload=None))
    await broker.handle_event(module.Event(name=module.EventName.SYSTEM_ALERT, payload="system online"))

    assert websocket.sent_messages == [
        {
            "type": "event",
            "event_name": "system.alert",
            "event_id": websocket.sent_messages[0]["event_id"],
            "timestamp": websocket.sent_messages[0]["timestamp"],
            "source": None,
            "task_id": None,
            "payload": {},
        },
        {
            "type": "event",
            "event_name": "system.alert",
            "event_id": websocket.sent_messages[1]["event_id"],
            "timestamp": websocket.sent_messages[1]["timestamp"],
            "source": None,
            "task_id": None,
            "payload": {},
        },
    ]


@pytest.mark.asyncio
async def test_robot_id_filter_does_not_bypass_station_filter(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    websocket = FakeWebSocket()
    module.register_websocket_forwarding_event(module.EventName.TASK_STATUS_CHANGED)

    await broker.connect(websocket, token=TEST_OPERATOR_TOKEN, station_id="A", robot_id="robot_01")
    event = module.Event(
        name=module.EventName.TASK_STATUS_CHANGED,
        payload={"station_id": "B", "robot_id": "robot_01", "status": "dispatched"},
    )

    await broker.handle_event(event)

    assert websocket.sent_messages == []


@pytest.mark.asyncio
async def test_multiple_filtered_clients_receive_only_their_robot_events(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    robot_01_ws = FakeWebSocket()
    robot_02_ws = FakeWebSocket()
    supervisor_ws = FakeWebSocket()

    await broker.connect(robot_01_ws, token=TEST_OPERATOR_TOKEN, robot_id="robot_01")
    await broker.connect(robot_02_ws, token=TEST_OPERATOR_TOKEN, robot_id="robot_02")
    await broker.connect(supervisor_ws, token=TEST_SUPERVISOR_TOKEN)

    await broker.handle_event(module.Event(name=module.EventName.QUADRUPED_TELEMETRY, payload={"robot_id": "robot_01"}))
    await broker.handle_event(module.Event(name=module.EventName.QUADRUPED_TELEMETRY, payload={"robot_id": "robot_02"}))

    assert [message["payload"]["robot_id"] for message in robot_01_ws.sent_messages] == ["robot_01"]
    assert [message["payload"]["robot_id"] for message in robot_02_ws.sent_messages] == ["robot_02"]
    assert [message["payload"]["robot_id"] for message in supervisor_ws.sent_messages] == ["robot_01", "robot_02"]


def test_patrol_events_are_relevant_to_websocket_broker(ws_module) -> None:
    module, _event_bus = ws_module

    module.clear_websocket_forwarding_events()
    module.register_platform_websocket_events()

    assert module.EventName.QUADRUPED_TELEMETRY in module.get_registered_websocket_events()
    assert module.EventName.PATROL_CYCLE_STARTED not in module.get_registered_websocket_events()
    assert module.EventName.PATROL_CYCLE_COMPLETED not in module.get_registered_websocket_events()
    assert module.EventName.PATROL_CYCLE_FAILED not in module.get_registered_websocket_events()
    assert module.EventName.PATROL_WAYPOINT_OBSERVED not in module.get_registered_websocket_events()
    assert module.EventName.PATROL_ANOMALY_DETECTED not in module.get_registered_websocket_events()
    assert module.EventName.PATROL_ANOMALY_CLEARED not in module.get_registered_websocket_events()
    assert module.EventName.PATROL_SUSPENDED not in module.get_registered_websocket_events()
    assert module.EventName.PATROL_RESUMED not in module.get_registered_websocket_events()


def test_logistics_websocket_registration_lives_in_app_layer(ws_module) -> None:
    module, _event_bus = ws_module
    from apps.logistics.observability.websocket import register_logistics_websocket_events

    module.clear_websocket_forwarding_events()
    register_logistics_websocket_events()

    registered_events = module.get_registered_websocket_events()

    assert module.EventName.TASK_STATUS_CHANGED in registered_events
    assert module.EventName.TASK_FAILED in registered_events
    assert module.EventName.PATROL_CYCLE_FAILED not in registered_events

    module.clear_websocket_forwarding_events()
    module.register_platform_websocket_events()


def test_patrol_websocket_registration_lives_in_app_layer(ws_module) -> None:
    module, _event_bus = ws_module
    from apps.patrol.observability.websocket import register_patrol_websocket_events

    module.clear_websocket_forwarding_events()
    register_patrol_websocket_events()

    registered_events = module.get_registered_websocket_events()

    assert module.EventName.PATROL_CYCLE_STARTED in registered_events
    assert module.EventName.PATROL_CYCLE_FAILED in registered_events
    assert module.EventName.PATROL_WAYPOINT_OBSERVED in registered_events
    assert module.EventName.TASK_FAILED not in registered_events

    module.clear_websocket_forwarding_events()
    module.register_platform_websocket_events()


@pytest.mark.asyncio
async def test_registered_websocket_event_is_forwarded(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    websocket = FakeWebSocket()
    module.clear_websocket_forwarding_events()
    module.register_websocket_forwarding_event("custom.workflow.updated")

    await broker.connect(websocket, token=TEST_OPERATOR_TOKEN)
    await broker.handle_event(module.Event(name="custom.workflow.updated", payload={"value": 1}, source="test"))

    assert websocket.sent_messages[0]["event_name"] == "custom.workflow.updated"
    assert websocket.sent_messages[0]["payload"] == {"value": 1}

    module.clear_websocket_forwarding_events()
    module.register_platform_websocket_events()


@pytest.mark.asyncio
async def test_failed_send_removes_client(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)
    failing_ws = FakeWebSocket()
    healthy_ws = FakeWebSocket()
    failing_ws.fail_send = True

    await broker.connect(failing_ws, token=TEST_OPERATOR_TOKEN, station_id="A")
    await broker.connect(healthy_ws, token=TEST_QA_TOKEN)

    await broker.broadcast({"type": "event", "payload": {"value": 1}})

    assert broker.client_count() == 1
    assert healthy_ws.sent_messages == [{"type": "event", "payload": {"value": 1}}]


@pytest.mark.asyncio
async def test_start_and_stop_are_idempotent(ws_module) -> None:
    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)

    await broker.start()
    await broker.start()
    assert event_bus.subscriber_count() == 1

    await broker.stop()
    await broker.stop()
    assert event_bus.subscriber_count() == 0


def test_websocket_endpoint_accepts_valid_token(ws_module, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    module, event_bus = ws_module
    broker = module.WebSocketBroker(event_bus=event_bus)

    import api.rest as rest_module

    monkeypatch.setattr(module, "get_ws_broker", lambda: broker)
    monkeypatch.setattr(rest_module, "get_ws_broker", lambda: broker)
    monkeypatch.setattr(rest_module, "startup_system", _noop_async)
    monkeypatch.setattr(rest_module, "shutdown_system", _noop_async)

    app = rest_module.create_app()

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?token={TEST_OPERATOR_TOKEN}&station_id=A&robot_id=robot_01") as websocket:
            websocket.send_text("ping")
            assert broker.client_count() == 1
            connected_client = next(iter(broker._clients.values()))
            assert connected_client.robot_id == "robot_01"

    only_client = next(iter(broker._clients.values()), None)
    assert only_client is None

    assert broker.client_count() == 0


async def _noop_async() -> None:
    return None
