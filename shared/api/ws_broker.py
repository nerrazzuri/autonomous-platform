from __future__ import annotations

"""WebSocket broker for real-time browser and operator updates."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, WebSocket, WebSocketDisconnect, status

from shared.api.auth import Role, get_auth_context
from shared.core.event_bus import Event, EventBus, EventName, get_event_bus
from shared.core.logger import get_logger


logger = get_logger(__name__)

_PLATFORM_WEBSOCKET_EVENTS = (
    EventName.QUADRUPED_TELEMETRY,
    EventName.SYSTEM_ALERT,
    EventName.BATTERY_WARN,
    EventName.BATTERY_CRITICAL,
    EventName.BATTERY_RECHARGED,
    EventName.QUADRUPED_ARRIVED_AT_WAYPOINT,
    EventName.NAVIGATION_BLOCKED,
    EventName.NAVIGATION_COMPLETED,
    EventName.NAVIGATION_FAILED,
    EventName.ESTOP_TRIGGERED,
    EventName.ESTOP_RELEASED,
)
_WEBSOCKET_EVENT_LOCK = threading.RLock()
_websocket_forwarding_events: set[str] = set()


def _event_name_key(event_name: EventName | str) -> str:
    return event_name.value if isinstance(event_name, EventName) else str(event_name)


def register_websocket_forwarding_event(event_name: EventName | str) -> None:
    normalized = _event_name_key(event_name).strip()
    if not normalized:
        raise ValueError("event_name must be a non-empty string")
    with _WEBSOCKET_EVENT_LOCK:
        _websocket_forwarding_events.add(normalized)


def unregister_websocket_forwarding_event(event_name: EventName | str) -> None:
    with _WEBSOCKET_EVENT_LOCK:
        _websocket_forwarding_events.discard(_event_name_key(event_name))


def clear_websocket_forwarding_events() -> None:
    with _WEBSOCKET_EVENT_LOCK:
        _websocket_forwarding_events.clear()


def get_registered_websocket_events() -> set[EventName | str]:
    registered: set[EventName | str] = set()
    with _WEBSOCKET_EVENT_LOCK:
        for event_name in _websocket_forwarding_events:
            try:
                registered.add(EventName(event_name))
            except ValueError:
                registered.add(event_name)
    return registered


def register_platform_websocket_events() -> None:
    for event_name in _PLATFORM_WEBSOCKET_EVENTS:
        register_websocket_forwarding_event(event_name)


def _is_websocket_forwarding_event(event_name: EventName | str) -> bool:
    with _WEBSOCKET_EVENT_LOCK:
        return _event_name_key(event_name) in _websocket_forwarding_events


register_platform_websocket_events()


class WebSocketBrokerError(Exception):
    """Raised when a WebSocket client cannot be authenticated or managed."""


@dataclass
class WebSocketClient:
    client_id: str
    websocket: WebSocket
    role: Role
    station_id: str | None = None
    robot_id: str | None = None
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class WebSocketBroker:
    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus or get_event_bus()
        self._clients: dict[str, WebSocketClient] = {}
        self._clients_lock = asyncio.Lock()
        self._subscription_ids: list[str] = []

    async def connect(
        self,
        websocket: WebSocket,
        token: str | None = None,
        station_id: str | None = None,
        robot_id: str | None = None,
    ) -> str:
        if token is None or not token.strip():
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            logger.warning("WebSocket connection rejected", extra={"reason": "missing_token"})
            raise WebSocketBrokerError("WebSocket token is required")

        try:
            auth_context = get_auth_context(f"Bearer {token}")
        except HTTPException as exc:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            logger.warning("WebSocket connection rejected", extra={"reason": "invalid_token"})
            raise WebSocketBrokerError("WebSocket authentication failed") from exc

        await websocket.accept()
        client_id = str(uuid4())
        client = WebSocketClient(
            client_id=client_id,
            websocket=websocket,
            role=auth_context.role,
            station_id=station_id,
            robot_id=robot_id,
        )
        async with self._clients_lock:
            self._clients[client_id] = client

        logger.info(
            "WebSocket client connected",
            extra={"client_id": client_id, "role": client.role.value, "station_id": station_id, "robot_id": robot_id},
        )
        return client_id

    async def disconnect(self, client_id: str) -> None:
        async with self._clients_lock:
            client = self._clients.pop(client_id, None)

        if client is None:
            return

        try:
            await client.websocket.close()
        except Exception:
            logger.debug("WebSocket close skipped", extra={"client_id": client_id})

        logger.info(
            "WebSocket client disconnected",
            extra={
                "client_id": client_id,
                "role": client.role.value,
                "station_id": client.station_id,
                "robot_id": client.robot_id,
            },
        )

    async def broadcast(
        self,
        message: dict[str, Any],
        *,
        station_id: str | None = None,
        robot_id: str | None = None,
        roles: set[Role] | None = None,
    ) -> None:
        async with self._clients_lock:
            clients = list(self._clients.values())

        failed_client_ids: list[str] = []
        for client in clients:
            if not self._should_send(client, station_id=station_id, robot_id=robot_id, roles=roles):
                continue
            try:
                await client.websocket.send_json(message)
            except Exception:
                logger.warning(
                    "WebSocket send failed",
                    extra={
                        "client_id": client.client_id,
                        "role": client.role.value,
                        "station_id": client.station_id,
                        "robot_id": client.robot_id,
                    },
                )
                failed_client_ids.append(client.client_id)

        for client_id in failed_client_ids:
            await self.disconnect(client_id)

    async def handle_event(self, event: Event) -> None:
        if not _is_websocket_forwarding_event(event.name):
            return

        payload = event.payload if isinstance(event.payload, dict) else {}
        station_id = payload.get("station_id")
        robot_id = payload.get("robot_id")
        message = {
            "type": "event",
            "event_name": event.name.value if hasattr(event.name, "value") else str(event.name),
            "event_id": event.event_id,
            "timestamp": event.timestamp.isoformat(),
            "source": event.source,
            "task_id": event.task_id,
            "payload": dict(payload),
        }
        await self.broadcast(message, station_id=station_id, robot_id=robot_id)

    async def start(self) -> None:
        async with self._clients_lock:
            if self._subscription_ids:
                return
            subscription_id = self._event_bus.subscribe("*", self.handle_event, subscriber_name="websocket_broker")
            self._subscription_ids = [subscription_id]

        logger.info("WebSocket broker started", extra={"subscription_count": 1})

    async def stop(self) -> None:
        async with self._clients_lock:
            subscription_ids = list(self._subscription_ids)
            self._subscription_ids.clear()
            client_ids = list(self._clients.keys())

        for subscription_id in subscription_ids:
            self._event_bus.unsubscribe(subscription_id)

        for client_id in client_ids:
            await self.disconnect(client_id)

        logger.info(
            "WebSocket broker stopped",
            extra={"subscription_count": len(subscription_ids), "disconnected_clients": len(client_ids)},
        )

    def client_count(self) -> int:
        return len(self._clients)

    def _should_send(
        self,
        client: WebSocketClient,
        *,
        station_id: str | None,
        robot_id: str | None,
        roles: set[Role] | None,
    ) -> bool:
        if roles is not None and client.role not in roles:
            return False

        if station_id is not None and client.role not in {Role.SUPERVISOR, Role.QA} and client.station_id != station_id:
            return False

        if client.robot_id is None or robot_id is None:
            return True
        return client.robot_id == robot_id


async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token")
    station_id = websocket.query_params.get("station_id")
    robot_id = websocket.query_params.get("robot_id")
    broker = get_ws_broker()
    try:
        client_id = await broker.connect(websocket, token=token, station_id=station_id, robot_id=robot_id)
    except WebSocketBrokerError:
        return

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await broker.disconnect(client_id)


ws_broker = WebSocketBroker()


def get_ws_broker() -> WebSocketBroker:
    return ws_broker


__all__ = [
    "clear_websocket_forwarding_events",
    "Event",
    "EventName",
    "Role",
    "WebSocketBroker",
    "WebSocketBrokerError",
    "WebSocketClient",
    "get_registered_websocket_events",
    "get_ws_broker",
    "register_platform_websocket_events",
    "register_websocket_forwarding_event",
    "unregister_websocket_forwarding_event",
    "websocket_endpoint",
    "ws_broker",
]
