from __future__ import annotations

"""Lightweight WebSocket client skeleton for the quadruped-side HMI agent."""

import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from apps.hmi_agent.mapper import HmiMappedAction


class TjcHmiWebSocketClient:
    def __init__(self, url: str, robot_id: str, screen_id: str, token: str) -> None:
        self.url = url
        self.robot_id = robot_id
        self.screen_id = screen_id
        self.token = token

    def url_with_token(self) -> str:
        parts = urlsplit(self.url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["token"] = self.token
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def build_action_payload(self, mapped_action: HmiMappedAction) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "robot_id": self.robot_id,
            "screen_id": self.screen_id,
            "action": mapped_action.action,
        }
        if mapped_action.station_id is not None:
            payload["station_id"] = mapped_action.station_id
        if mapped_action.destination is not None:
            payload["destination_id"] = mapped_action.destination
        if mapped_action.task_id is not None:
            payload["task_id"] = mapped_action.task_id
        if mapped_action.route_id is not None:
            payload["route_id"] = mapped_action.route_id
        return payload

    async def send_action(self, mapped_action: HmiMappedAction) -> dict[str, Any]:
        websockets = _import_websockets()
        async with websockets.connect(self.url_with_token()) as websocket:
            await websocket.send(json.dumps(self.build_action_payload(mapped_action)))
            raw = await websocket.recv()
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("HMI WebSocket response must be a JSON object")
        return decoded

    async def run_forever(self) -> None:
        """Placeholder for the future UART-to-WebSocket daemon loop."""
        raise NotImplementedError(
            "The production serial loop belongs to a later module; use send_action() for prototype calls."
        )


def _import_websockets():
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError(
            "The optional 'websockets' package is required to connect to /hmi/ws at runtime."
        ) from exc
    return websockets


__all__ = ["TjcHmiWebSocketClient"]
