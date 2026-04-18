from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any

from autonomous_logistic.core.models import Capabilities, Station


def parse_bool(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RobotSettings:
    adapter: str = "mock"
    sdk_module_name: str = "agibot_sdk"
    robot_ip: str = "192.168.168.168"
    client_ip: str = "127.0.0.1"
    sdk_port: int = 43988
    control_level: str = "high"


@dataclass(frozen=True)
class AppSettings:
    app_name: str = "Autonomous Logistic"
    app_mode: str = "mock"
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    db_path: str = "data/autonomous_logistic.sqlite3"
    robot: RobotSettings = field(default_factory=RobotSettings)
    capabilities: Capabilities = field(default_factory=Capabilities)
    stations: list[Station] = field(default_factory=lambda: [
        Station("STATION_A", "Station A", {"type": "generic", "label": "A"}, {}),
        Station("STATION_B", "Station B", {"type": "generic", "label": "B"}, {}),
    ])

    @classmethod
    def from_sources(cls, config_path: str | None = "config/app.local.json") -> "AppSettings":
        raw: dict[str, Any] = {}
        if config_path and Path(config_path).exists():
            raw = json.loads(Path(config_path).read_text(encoding="utf-8"))

        capabilities_data = raw.get("capabilities", {})
        robot_data = raw.get("robot", {})
        station_data = raw.get("stations", [])

        capabilities = Capabilities(
            has_lidar=parse_bool(os.getenv("AL_HAS_LIDAR"), bool(capabilities_data.get("has_lidar", False))),
            has_speaker=parse_bool(os.getenv("AL_HAS_SPEAKER"), bool(capabilities_data.get("has_speaker", False))),
            has_screen=parse_bool(os.getenv("AL_HAS_SCREEN"), bool(capabilities_data.get("has_screen", False))),
            has_touch_input=parse_bool(os.getenv("AL_HAS_TOUCH_INPUT"), bool(capabilities_data.get("has_touch_input", False))),
            has_button_panel=parse_bool(os.getenv("AL_HAS_BUTTON_PANEL"), bool(capabilities_data.get("has_button_panel", False))),
            has_remote_dispatch=parse_bool(os.getenv("AL_HAS_REMOTE_DISPATCH"), bool(capabilities_data.get("has_remote_dispatch", True))),
            has_local_hmi=parse_bool(os.getenv("AL_HAS_LOCAL_HMI"), bool(capabilities_data.get("has_local_hmi", False))),
        )
        robot = RobotSettings(
            adapter=os.getenv("AL_ROBOT_ADAPTER", robot_data.get("adapter", "mock")),
            sdk_module_name=os.getenv("AL_SDK_MODULE_NAME", robot_data.get("sdk_module_name", "agibot_sdk")),
            robot_ip=os.getenv("AL_ROBOT_IP", robot_data.get("robot_ip", "192.168.168.168")),
            client_ip=os.getenv("AL_CLIENT_IP", robot_data.get("client_ip", "127.0.0.1")),
            sdk_port=int(os.getenv("AL_SDK_PORT", robot_data.get("sdk_port", 43988))),
            control_level=os.getenv("AL_CONTROL_LEVEL", robot_data.get("control_level", "high")),
        )
        stations = [
            Station(
                station_id=item["station_id"],
                name=item["name"],
                position=item.get("position", {}),
                metadata=item.get("metadata", {}),
            )
            for item in station_data
        ] or cls().stations

        return cls(
            app_name=os.getenv("AL_APP_NAME", raw.get("app_name", "Autonomous Logistic")),
            app_mode=os.getenv("AL_APP_MODE", raw.get("app_mode", robot.adapter)),
            bind_host=os.getenv("AL_BIND_HOST", raw.get("bind_host", "127.0.0.1")),
            bind_port=int(os.getenv("AL_BIND_PORT", raw.get("bind_port", 8000))),
            db_path=os.getenv("AL_DB_PATH", raw.get("db_path", "data/autonomous_logistic.sqlite3")),
            robot=robot,
            capabilities=capabilities,
            stations=stations,
        )
