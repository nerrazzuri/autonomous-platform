from __future__ import annotations

"""Configuration objects for the prototype TJC HMI agent."""

from dataclasses import dataclass, field

from apps.hmi_agent.mapper import DEFAULT_BUTTON_MAPPING


@dataclass(frozen=True)
class TjcHmiAgentConfig:
    serial_port: str = "/dev/tjc_hmi"
    baudrate: int = 115200
    ws_url: str = "ws://127.0.0.1:8000/hmi/ws"
    robot_id: str = "robot-1"
    screen_id: str = "screen-front"
    token: str = ""
    button_mapping: dict = field(default_factory=lambda: dict(DEFAULT_BUTTON_MAPPING))


__all__ = ["TjcHmiAgentConfig"]
