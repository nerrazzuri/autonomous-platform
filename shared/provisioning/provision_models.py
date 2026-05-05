from __future__ import annotations

from dataclasses import dataclass

from shared.provisioning.roles import validate_role


def _require_non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _validate_role(value: object) -> str:
    return validate_role(_require_non_empty_string(value, "role"))


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    signal: int | None = None
    security: str | None = None
    is_robot_ap: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "ssid", _require_non_empty_string(self.ssid, "ssid"))
        if self.signal is not None and type(self.signal) is not int:
            raise ValueError("signal must be an int or None")
        if self.security is not None:
            object.__setattr__(
                self,
                "security",
                _require_non_empty_string(self.security, "security"),
            )


@dataclass(frozen=True)
class ProvisionRequest:
    quadruped_ap_ssid: str
    target_wifi_ssid: str
    target_wifi_password: str
    role: str = "logistics"
    pc_wifi_iface: str | None = None
    robot_id: str | None = None
    ssh_user: str = "firefly"
    ssh_password: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "quadruped_ap_ssid",
            _require_non_empty_string(self.quadruped_ap_ssid, "quadruped_ap_ssid"),
        )
        object.__setattr__(
            self,
            "target_wifi_ssid",
            _require_non_empty_string(self.target_wifi_ssid, "target_wifi_ssid"),
        )
        object.__setattr__(
            self,
            "target_wifi_password",
            _require_non_empty_string(self.target_wifi_password, "target_wifi_password"),
        )
        object.__setattr__(self, "role", _validate_role(self.role))
        object.__setattr__(
            self,
            "ssh_user",
            _require_non_empty_string(self.ssh_user, "ssh_user"),
        )
        if self.pc_wifi_iface is not None:
            object.__setattr__(
                self,
                "pc_wifi_iface",
                _require_non_empty_string(self.pc_wifi_iface, "pc_wifi_iface"),
            )
        if self.robot_id is not None:
            object.__setattr__(
                self,
                "robot_id",
                _require_non_empty_string(self.robot_id, "robot_id"),
            )
        if self.ssh_password is not None:
            object.__setattr__(
                self,
                "ssh_password",
                _require_non_empty_string(self.ssh_password, "ssh_password"),
            )


@dataclass(frozen=True)
class ProvisionResult:
    success: bool
    robot_id: str | None = None
    quadruped_mac: str | None = None
    quadruped_ip: str | None = None
    pc_ip: str | None = None
    role: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if type(self.success) is not bool:
            raise ValueError("success must be a bool")
        if self.robot_id is not None:
            object.__setattr__(
                self,
                "robot_id",
                _require_non_empty_string(self.robot_id, "robot_id"),
            )
        if self.quadruped_mac is not None:
            object.__setattr__(
                self,
                "quadruped_mac",
                _require_non_empty_string(self.quadruped_mac, "quadruped_mac"),
            )
        if self.quadruped_ip is not None:
            object.__setattr__(
                self,
                "quadruped_ip",
                _require_non_empty_string(self.quadruped_ip, "quadruped_ip"),
            )
        if self.pc_ip is not None:
            object.__setattr__(
                self,
                "pc_ip",
                _require_non_empty_string(self.pc_ip, "pc_ip"),
            )
        if self.role is not None:
            object.__setattr__(self, "role", _validate_role(self.role))
        if self.message is not None:
            object.__setattr__(
                self,
                "message",
                _require_non_empty_string(self.message, "message"),
            )


@dataclass(frozen=True)
class RobotConnectionConfig:
    robot_id: str
    quadruped_ip: str
    pc_ip: str | None = None
    sdk_port: int | None = None
    ssh_user: str = "firefly"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "robot_id",
            _require_non_empty_string(self.robot_id, "robot_id"),
        )
        object.__setattr__(self, "quadruped_ip", _require_non_empty_string(self.quadruped_ip, "quadruped_ip"))
        object.__setattr__(
            self,
            "ssh_user",
            _require_non_empty_string(self.ssh_user, "ssh_user"),
        )
        if self.pc_ip is not None:
            object.__setattr__(self, "pc_ip", _require_non_empty_string(self.pc_ip, "pc_ip"))
        if self.sdk_port is not None and (type(self.sdk_port) is not int or self.sdk_port <= 0):
            raise ValueError("sdk_port must be a positive int or None")


@dataclass(frozen=True)
class RobotStatus:
    robot_id: str
    quadruped_ip: str | None = None
    connected: bool = False
    provisioned: bool = False
    message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "robot_id",
            _require_non_empty_string(self.robot_id, "robot_id"),
        )
        if self.quadruped_ip is not None:
            object.__setattr__(self, "quadruped_ip", _require_non_empty_string(self.quadruped_ip, "quadruped_ip"))
        if type(self.connected) is not bool:
            raise ValueError("connected must be a bool")
        if type(self.provisioned) is not bool:
            raise ValueError("provisioned must be a bool")
        if self.message is not None:
            object.__setattr__(
                self,
                "message",
                _require_non_empty_string(self.message, "message"),
            )
