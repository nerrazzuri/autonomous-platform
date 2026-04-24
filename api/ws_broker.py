from __future__ import annotations

"""WebSocket broker for real-time browser and operator updates."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, WebSocket, WebSocketDisconnect, status

from api.auth import Role, get_auth_context
from core.event_bus import Event, EventBus, EventName, get_event_bus
from core.logger import get_logger


logger = get_logger(__name__)

_RELEVANT_EVENT_NAMES = {
    EventName.QUADRUPED_TELEMETRY,
    EventName.TASK_STATUS_CHANGED,
    EventName.TASK_SUBMITTED,
    EventName.TASK_DISPATCHED,
    EventName.TASK_COMPLETED,
    EventName.TASK_FAILED,
    EventName.TASK_CANCELLED,
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
}


class WebSocketBrokerError(Exception):
    """Raised when a WebSocket client cannot be authenticated or managed."""


@dataclass
class WebSocketClient:
    client_id: str
    websocket: WebSocket
    role: Role
    station_id: str | None = None
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class WebSocketBroker:
    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus or get_event_bus()
        self._clients: dict[str, WebSocketClient] = {}
        self._clients_lock = asyncio.Lock()
        self._subscription_ids: list[str] = []

    async def connect(self, websocket: WebSocket, token: str | None = None, station_id: str | None = None) -> str:
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
        )
        async with self._clients_lock:
            self._clients[client_id] = client

        logger.info(
            "WebSocket client connected",
            extra={"client_id": client_id, "role": client.role.value, "station_id": station_id},
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
            extra={"client_id": client_id, "role": client.role.value, "station_id": client.station_id},
        )

    async def broadcast(
        self,
        message: dict[str, Any],
        *,
        station_id: str | None = None,
        roles: set[Role] | None = None,
    ) -> None:
        async with self._clients_lock:
            clients = list(self._clients.values())

        failed_client_ids: list[str] = []
        for client in clients:
            if not self._should_send(client, station_id=station_id, roles=roles):
                continue
            try:
                await client.websocket.send_json(message)
            except Exception:
                logger.warning(
                    "WebSocket send failed",
                    extra={"client_id": client.client_id, "role": client.role.value, "station_id": client.station_id},
                )
                failed_client_ids.append(client.client_id)

        for client_id in failed_client_ids:
            await self.disconnect(client_id)

    async def handle_event(self, event: Event) -> None:
        if event.name not in _RELEVANT_EVENT_NAMES:
            return

        station_id = event.payload.get("station_id")
        message = {
            "type": "event",
            "event_name": event.name.value,
            "event_id": event.event_id,
            "timestamp": event.timestamp.isoformat(),
            "source": event.source,
            "task_id": event.task_id,
            "payload": dict(event.payload),
        }
        await self.broadcast(message, station_id=station_id)

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

    def _should_send(self, client: WebSocketClient, *, station_id: str | None, roles: set[Role] | None) -> bool:
        if roles is not None and client.role not in roles:
            return False

        if station_id is None:
            return True

        if client.role in {Role.SUPERVISOR, Role.QA}:
            return True
        return client.station_id == station_id


async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token")
    station_id = websocket.query_params.get("station_id")
    broker = get_ws_broker()
    try:
        client_id = await broker.connect(websocket, token=token, station_id=station_id)
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
    "Event",
    "EventName",
    "Role",
    "WebSocketBroker",
    "WebSocketBrokerError",
    "WebSocketClient",
    "get_ws_broker",
    "websocket_endpoint",
    "ws_broker",
]
