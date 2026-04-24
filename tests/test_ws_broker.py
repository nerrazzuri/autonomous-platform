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
    return module, EventBus()


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

    client_id = await broker.connect(websocket, token=TEST_OPERATOR_TOKEN, station_id="A")

    assert isinstance(client_id, str)
    assert websocket.accepted is True
    assert broker.client_count() == 1
    assert broker._clients[client_id].role is module.Role.OPERATOR
    assert broker._clients[client_id].station_id == "A"


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

    app = rest_module.create_app()

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?token={TEST_OPERATOR_TOKEN}&station_id=A") as websocket:
            websocket.send_text("ping")
            assert broker.client_count() == 1

    assert broker.client_count() == 0

