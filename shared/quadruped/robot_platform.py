from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.core.robot_config import RobotConfig


class RobotPlatformError(Exception):
    """Raised when a robot platform container is invalid."""


@dataclass
class RobotPlatform:
    robot_id: str
    config: RobotConfig
    sdk_adapter: Any
    heartbeat: Any
    state_monitor: Any
    navigator: Any

    def __post_init__(self) -> None:
        if not isinstance(self.robot_id, str) or not self.robot_id.strip():
            raise RobotPlatformError("robot_id must be a non-empty string")
        if not isinstance(self.config, RobotConfig):
            raise RobotPlatformError("config must be a RobotConfig")
        if self.sdk_adapter is None:
            raise RobotPlatformError("sdk_adapter must not be None")
        if self.heartbeat is None:
            raise RobotPlatformError("heartbeat must not be None")
        if self.state_monitor is None:
            raise RobotPlatformError("state_monitor must not be None")
        if self.navigator is None:
            raise RobotPlatformError("navigator must not be None")
        if self.robot_id != self.config.connection.robot_id:
            raise RobotPlatformError("robot_id must match config.connection.robot_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "robot_ip": self.config.connection.robot_ip,
            "sdk_port": self.config.connection.sdk_port,
            "local_ip": self.config.connection.local_ip,
            "local_port": self.config.connection.local_port,
            "capabilities": {
                "lidar": self.config.capabilities.lidar,
                "camera": self.config.capabilities.camera,
                "speaker": self.config.capabilities.speaker,
                "screen": self.config.capabilities.screen,
            },
        }
