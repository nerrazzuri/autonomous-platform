from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


DEFAULT_SDK_PORT = 43988
DEFAULT_LOCAL_IP = "127.0.0.1"
DEFAULT_LOCAL_PORT_BASE = 50050


class RobotConfigError(Exception):
    """Raised when robot configuration cannot be read or validated."""


def _is_valid_ipv4(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part or not part.isdigit():
            return False
        if len(part) > 1 and part.startswith("0"):
            return False
        number = int(part)
        if number < 0 or number > 255:
            return False
    return True


def _is_valid_port(value: object) -> bool:
    return type(value) is int and 1 <= value <= 65535


@dataclass(frozen=True)
class RobotConnectionConfig:
    robot_id: str
    robot_ip: str
    sdk_port: int
    local_ip: str
    local_port: int

    def __post_init__(self) -> None:
        if not isinstance(self.robot_id, str) or not self.robot_id.strip():
            raise RobotConfigError("connection.robot_id must be a non-empty string")
        if not _is_valid_ipv4(self.robot_ip):
            raise RobotConfigError("connection.robot_ip must be a valid IPv4 address")
        if not _is_valid_port(self.sdk_port):
            raise RobotConfigError("connection.sdk_port must be between 1 and 65535")
        if not _is_valid_ipv4(self.local_ip):
            raise RobotConfigError("connection.local_ip must be a valid IPv4 address")
        if self.local_ip == "0.0.0.0":
            raise RobotConfigError("connection.local_ip must not be 0.0.0.0")
        if not _is_valid_port(self.local_port):
            raise RobotConfigError("connection.local_port must be between 1 and 65535")


@dataclass(frozen=True)
class RobotCapabilityConfig:
    lidar: bool = False
    camera: bool = False
    speaker: bool = False
    screen: bool = False


@dataclass(frozen=True)
class RobotConfig:
    connection: RobotConnectionConfig
    capabilities: RobotCapabilityConfig
    display_name: str | None = None
    mac: str | None = None
    role: str | None = None
    sdk_lib_path: str | None = None
    enabled: bool = True

    @property
    def quadruped_ip(self) -> str:
        return self.connection.robot_ip


class RobotConfigLoader:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else Path("data/robots.yaml")

    def load(self) -> list[RobotConfig]:
        config_path = self.path
        if not config_path.exists():
            raise RobotConfigError(f"Robot config file '{config_path}' does not exist")

        try:
            with config_path.open("r", encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            raise RobotConfigError(f"Failed to parse robot config file '{config_path}': {exc}") from exc
        except OSError as exc:
            raise RobotConfigError(f"Failed to read robot config file '{config_path}': {exc}") from exc

        if loaded is None:
            raise RobotConfigError(f"Robot config file '{config_path}' is empty")

        if isinstance(loaded, dict):
            loaded = loaded.get("robots")
            if loaded is None:
                raise RobotConfigError(
                    f"Robot config file '{config_path}' must contain a top-level list or 'robots' list"
                )
        elif not isinstance(loaded, list):
            raise RobotConfigError(
                f"Robot config file '{config_path}' must contain a top-level list or 'robots' list"
            )
        if not isinstance(loaded, list):
            raise RobotConfigError(f"Robot config file '{config_path}' must contain a list of robots")

        configs: list[RobotConfig] = []
        seen_robot_ids: set[str] = set()
        for index, item in enumerate(loaded, start=1):
            configs.append(self._build_robot_config(item, index, seen_robot_ids))
        return configs

    def _build_robot_config(
        self, item: object, index: int, seen_robot_ids: set[str]
    ) -> RobotConfig:
        if not isinstance(item, dict):
            raise RobotConfigError(f"Robot entry {index} must be a mapping")
        if "robot_id" not in item:
            raise RobotConfigError(f"Robot entry {index} is missing robot_id")
        if "connection" in item:
            connection_data = item["connection"]
        elif "quadruped_ip" in item or "robot_ip" in item:
            connection_data = {
                "robot_ip": item.get("quadruped_ip", item.get("robot_ip")),
                "sdk_port": item.get("sdk_port", DEFAULT_SDK_PORT),
                "local_ip": item.get("local_ip", DEFAULT_LOCAL_IP),
                "local_port": item.get("local_port", DEFAULT_LOCAL_PORT_BASE + index),
            }
        else:
            raise RobotConfigError(f"Robot entry {index} is missing connection")
        capabilities_data = item.get("capabilities", {})

        if not isinstance(connection_data, dict):
            raise RobotConfigError(f"Robot entry {index} connection must be a mapping")
        if not isinstance(capabilities_data, dict):
            raise RobotConfigError(f"Robot entry {index} capabilities must be a mapping")

        try:
            connection = RobotConnectionConfig(robot_id=item["robot_id"], **connection_data)
        except TypeError as exc:
            raise RobotConfigError(f"Robot entry {index} has invalid connection fields: {exc}") from exc
        try:
            capabilities = RobotCapabilityConfig(**capabilities_data)
        except TypeError as exc:
            raise RobotConfigError(f"Robot entry {index} has invalid capability fields: {exc}") from exc

        if connection.robot_id in seen_robot_ids:
            raise RobotConfigError(f"Robot entry {index} has duplicate robot_id '{connection.robot_id}'")

        seen_robot_ids.add(connection.robot_id)
        return RobotConfig(
            connection=connection,
            capabilities=capabilities,
            display_name=item.get("display_name"),
            mac=item.get("mac"),
            role=item.get("role"),
            sdk_lib_path=item.get("sdk_lib_path"),
            enabled=bool(item.get("enabled", True)),
        )


_ROBOT_CONFIG_CACHE: list[RobotConfig] | None = None


def get_robot_configs() -> list[RobotConfig]:
    global _ROBOT_CONFIG_CACHE
    if _ROBOT_CONFIG_CACHE is None:
        _ROBOT_CONFIG_CACHE = RobotConfigLoader().load()
    return _ROBOT_CONFIG_CACHE
