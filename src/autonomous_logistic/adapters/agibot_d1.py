from importlib import import_module

from autonomous_logistic.adapters.robot import NavigationResult
from autonomous_logistic.core.errors import RobotAdapterUnavailable


class AgibotD1Adapter:
    """Boundary for future high-level Agibot D1 SDK integration."""

    def __init__(
        self,
        sdk_module_name: str,
        robot_ip: str = "192.168.168.168",
        client_ip: str = "127.0.0.1",
        sdk_port: int = 43988,
        control_level: str = "high",
    ) -> None:
        if control_level != "high":
            raise RobotAdapterUnavailable(
                f"Agibot D1 adapter requires robot.control_level 'high'; got '{control_level}'."
            )
        self.sdk_module_name = sdk_module_name
        self.robot_ip = robot_ip
        self.client_ip = client_ip
        self.sdk_port = sdk_port
        self.control_level = control_level
        self.sdk_module = None

    def connect(self) -> None:
        try:
            self.sdk_module = import_module(self.sdk_module_name)
        except ImportError as exc:
            raise RobotAdapterUnavailable(
                f"Agibot SDK module '{self.sdk_module_name}' is not installed. "
                "Install the vendor SDK and configure robot_ip, client_ip, and sdk_port before selecting agibot_d1 mode."
            ) from exc

    def move(self, direction: str, speed: float) -> NavigationResult:
        self._raise_not_wired()

    def stop(self) -> NavigationResult:
        self._raise_not_wired()

    def pause(self) -> NavigationResult:
        self._raise_not_wired()

    def resume(self) -> NavigationResult:
        self._raise_not_wired()

    def navigate_to(self, target: str) -> NavigationResult:
        self._raise_not_wired()

    def get_sensor_status(self) -> dict:
        return {"adapter": "agibot_d1", "connected": self.sdk_module is not None}

    def get_health_status(self) -> dict:
        return {
            "mode": "agibot_d1",
            "connected": self.sdk_module is not None,
            "robot_ip": self.robot_ip,
            "client_ip": self.client_ip,
            "sdk_port": self.sdk_port,
            "control_level": self.control_level,
        }

    def _raise_not_wired(self) -> NavigationResult:
        raise RobotAdapterUnavailable(
            "AgibotD1Adapter is a verified integration seam only. "
            "Wire vendor high-level SDK calls after the SDK package is installed."
        )
