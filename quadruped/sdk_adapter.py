from __future__ import annotations

"""Async-safe adapter around the vendor quadruped SDK."""

import asyncio
import importlib
import math
import platform
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from core.config import get_config
from core.event_bus import EventName, get_event_bus
from core.logger import get_logger


logger = get_logger(__name__)


class SDKAdapterError(Exception):
    """Base exception for quadruped SDK adapter failures."""


class SDKUnavailableError(SDKAdapterError):
    """Raised when the vendor SDK is unavailable and mock mode is disabled."""


class InvalidQuadrupedStateError(SDKAdapterError):
    """Raised for invalid quadruped state transitions."""


class QuadrupedMode(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    PASSIVE = "passive"
    STANDING = "standing"
    MOVING = "moving"
    ERROR = "error"


@dataclass(frozen=True)
class QuadrupedPose:
    x: float
    y: float
    z: float
    roll: float
    pitch: float
    yaw: float


@dataclass(frozen=True)
class QuadrupedTelemetrySnapshot:
    battery_pct: int
    position: tuple[float, float, float]
    rpy: tuple[float, float, float]
    control_mode: int
    connection_ok: bool
    mode: QuadrupedMode


class _NullSDKClient:
    def initRobot(self, local_ip, port, quadruped_ip):
        return True

    def passive(self):
        return True

    def standUp(self):
        return True

    def lieDown(self):
        return True

    def move(self, vx, vy, yaw_rate):
        return True

    def getPosition(self):
        return (0.0, 0.0, 0.0)

    def getRPY(self):
        return (0.0, 0.0, 0.0)

    def getBattery(self):
        return 100

    def getControlMode(self):
        return 0

    def checkConnect(self):
        return True


class SDKAdapter:
    def __init__(
        self,
        quadruped_ip: str | None = None,
        local_ip: str | None = None,
        sdk_port: int | None = None,
        sdk_lib_path: str | None = None,
        sdk_client: Any | None = None,
        allow_mock: bool = True,
    ):
        config = get_config()
        self._quadruped_ip = quadruped_ip or config.quadruped.quadruped_ip
        self._local_ip = local_ip or config.workstation.local_ip
        self._sdk_port = sdk_port or config.quadruped.sdk_port
        self._sdk_lib_path = sdk_lib_path if sdk_lib_path is not None else config.quadruped.sdk_lib_path
        self._max_forward_velocity = config.navigation.max_forward_velocity
        self._max_yaw_rate = config.navigation.max_yaw_rate
        self._allow_mock = allow_mock
        self._sdk_client = sdk_client if sdk_client is not None else self._create_sdk_client()
        self._mode = QuadrupedMode.DISCONNECTED
        self._last_command_velocity = (0.0, 0.0, 0.0)
        self._last_error: str | None = None

    async def connect(self) -> bool:
        if self._mode not in {QuadrupedMode.DISCONNECTED, QuadrupedMode.ERROR}:
            return True

        if self._local_ip == "0.0.0.0":
            logger.warning(
                "local_ip is 0.0.0.0; SDK initRobot requires the actual workstation IP",
                extra={"local_ip": self._local_ip},
            )

        result = await self._call_sdk(
            "initRobot",
            self._local_ip,
            self._sdk_port,
            self._quadruped_ip,
            default=False,
        )
        if not result:
            self._set_error("Failed to connect quadruped SDK client")
            return False

        self._set_mode(QuadrupedMode.CONNECTED)
        logger.info("Quadruped SDK connected", extra={"quadruped_ip": self._quadruped_ip})
        await self._publish_connection_event(EventName.QUADRUPED_CONNECTION_RESTORED)
        await self.passive()
        return True

    async def stand_up(self) -> bool:
        if self._mode not in {QuadrupedMode.CONNECTED, QuadrupedMode.PASSIVE}:
            self._set_error("stand_up requires CONNECTED or PASSIVE mode")
            logger.warning("Rejected stand_up command", extra={"mode": self._mode.value})
            return False

        result = await self._call_sdk("standUp", default=False)
        if not result:
            return False
        self._set_mode(QuadrupedMode.STANDING)
        return True

    async def lie_down(self) -> bool:
        if self._mode not in {QuadrupedMode.PASSIVE, QuadrupedMode.STANDING}:
            self._set_error("lie_down requires PASSIVE or STANDING mode")
            logger.warning("Rejected lie_down command", extra={"mode": self._mode.value})
            return False

        if not hasattr(self._sdk_client, "lieDown"):
            self._set_error("SDK client does not expose lieDown")
            logger.warning("SDK client missing lieDown")
            return False

        result = await self._call_sdk("lieDown", default=False)
        if not result:
            return False
        self._set_mode(QuadrupedMode.PASSIVE)
        return True

    async def passive(self, reason: str | None = None) -> bool:
        if self._mode not in {
            QuadrupedMode.CONNECTED,
            QuadrupedMode.PASSIVE,
            QuadrupedMode.STANDING,
            QuadrupedMode.MOVING,
            QuadrupedMode.ERROR,
        }:
            self._set_error("passive requires an initialized SDK connection")
            logger.warning("Rejected passive command", extra={"mode": self._mode.value})
            return False

        result = await self._call_sdk("passive", default=False)
        if not result:
            return False
        self._last_command_velocity = (0.0, 0.0, 0.0)
        self._set_mode(QuadrupedMode.PASSIVE)
        return True

    async def move(self, vx: float, vy: float, yaw_rate: float) -> bool:
        if self._mode not in {QuadrupedMode.STANDING, QuadrupedMode.MOVING}:
            self._set_error("move requires STANDING or MOVING mode")
            logger.warning("Rejected move command", extra={"mode": self._mode.value})
            return False

        if not all(math.isfinite(value) for value in (vx, vy, yaw_rate)):
            self._set_error("move rejected non-finite velocity input")
            logger.warning("Rejected move command with non-finite velocity")
            return False

        clamped_vx = self._clamp(vx, self._max_forward_velocity)
        clamped_vy = self._clamp(vy, self._max_forward_velocity)
        clamped_yaw = self._clamp(yaw_rate, self._max_yaw_rate)

        result = await self._call_sdk("move", clamped_vx, clamped_vy, clamped_yaw, default=False)
        if not result:
            return False

        self._last_command_velocity = (clamped_vx, clamped_vy, clamped_yaw)
        if any(value != 0.0 for value in self._last_command_velocity):
            self._set_mode(QuadrupedMode.MOVING)
        else:
            self._set_mode(QuadrupedMode.STANDING)
        return True

    async def stop_motion(self) -> bool:
        return await self.move(0.0, 0.0, 0.0)

    async def get_position(self) -> tuple[float, float, float]:
        position = await self._call_sdk("getPosition", default=(0.0, 0.0, 0.0))
        return self._coerce_vector(position)

    async def get_rpy(self) -> tuple[float, float, float]:
        rpy = await self._call_sdk("getRPY", default=(0.0, 0.0, 0.0))
        return self._coerce_vector(rpy)

    async def get_battery(self) -> int:
        battery = await self._call_sdk_aliases(("getBatteryPower", "getBattery"), default=0)
        try:
            return int(battery)
        except (TypeError, ValueError):
            return 0

    async def get_control_mode(self) -> int:
        control_mode = await self._call_sdk_aliases(("getCurrentMode", "getControlMode"), default=-1)
        try:
            return int(control_mode)
        except (TypeError, ValueError):
            return -1

    async def check_connection(self) -> bool:
        connection_ok = bool(await self._call_sdk_aliases(("checkConnection", "checkConnect"), default=False))
        if not connection_ok:
            if self._mode != QuadrupedMode.DISCONNECTED:
                self._set_error("Quadruped SDK connection lost")
                self._set_mode(QuadrupedMode.ERROR)
                await self._publish_connection_event(EventName.QUADRUPED_CONNECTION_LOST)
            return False
        return True

    async def get_telemetry_snapshot(self) -> QuadrupedTelemetrySnapshot:
        connection_ok = await self.check_connection()
        position = await self.get_position()
        rpy = await self.get_rpy()
        battery_pct = await self.get_battery()
        control_mode = await self.get_control_mode()
        return QuadrupedTelemetrySnapshot(
            battery_pct=battery_pct,
            position=position,
            rpy=rpy,
            control_mode=control_mode,
            connection_ok=connection_ok,
            mode=self._mode if connection_ok else self._mode,
        )

    def current_mode(self) -> QuadrupedMode:
        return self._mode

    def last_error(self) -> str | None:
        return self._last_error

    async def _call_sdk(self, method_name: str, *args, default: Any = None) -> Any:
        method = getattr(self._sdk_client, method_name, None)
        if method is None:
            self._set_error(f"SDK client missing method: {method_name}")
            logger.error("SDK method missing", extra={"method_name": method_name})
            return default

        try:
            return await asyncio.to_thread(method, *args)
        except Exception as exc:
            self._set_error(f"{method_name} failed: {exc}")
            logger.exception("SDK call failed", extra={"method_name": method_name})
            return default

    async def _call_sdk_aliases(self, method_names: tuple[str, ...], *args, default: Any = None) -> Any:
        last_missing_name = method_names[-1]
        for method_name in method_names:
            method = getattr(self._sdk_client, method_name, None)
            if method is None:
                continue
            try:
                return await asyncio.to_thread(method, *args)
            except Exception as exc:
                self._set_error(f"{method_name} failed: {exc}")
                logger.exception("SDK call failed", extra={"method_name": method_name})
                return default

        self._set_error(f"SDK client missing method: {last_missing_name}")
        logger.error("SDK method missing", extra={"method_name": last_missing_name, "aliases": method_names})
        return default

    def _create_sdk_client(self) -> Any:
        try:
            self._prepare_sdk_import_path()
            sdk_module = importlib.import_module("mc_sdk_zsl_1_py")
            return sdk_module.HighLevel()
        except Exception as exc:
            if self._allow_mock:
                logger.info("Vendor SDK unavailable, using null quadruped client")
                return _NullSDKClient()
            raise SDKUnavailableError(f"Vendor SDK unavailable: {exc}") from exc

    def _prepare_sdk_import_path(self) -> None:
        if not self._sdk_lib_path:
            return

        arch = self._detect_sdk_architecture()
        sdk_arch_path = str(Path(self._sdk_lib_path) / arch)
        if sys.path and sys.path[0] == sdk_arch_path:
            return
        if sdk_arch_path in sys.path:
            sys.path.remove(sdk_arch_path)
        sys.path.insert(0, sdk_arch_path)

    def _detect_sdk_architecture(self) -> str:
        machine = platform.machine().lower()
        if machine in {"amd64", "x86_64"}:
            return "x86_64"
        if machine in {"arm64", "aarch64"}:
            return "aarch64"
        return machine

    async def _publish_connection_event(self, event_name: EventName) -> None:
        try:
            get_event_bus().publish_nowait(
                event_name,
                payload={"quadruped_state": self._mode.value},
                source=__name__,
            )
        except asyncio.QueueFull:
            logger.warning("Event bus queue full while publishing quadruped connection event")
        except Exception:
            logger.exception("Failed to publish quadruped connection event")

    def _set_mode(self, new_mode: QuadrupedMode) -> None:
        previous_mode = self._mode
        self._mode = new_mode
        if previous_mode != new_mode:
            logger.info(
                "Quadruped mode changed",
                extra={"from_mode": previous_mode.value, "to_mode": new_mode.value},
            )

    def _set_error(self, message: str) -> None:
        self._last_error = message

    def _clamp(self, value: float, limit: float) -> float:
        return max(-limit, min(limit, float(value)))

    def _coerce_vector(self, value: Any) -> tuple[float, float, float]:
        if isinstance(value, (list, tuple)) and len(value) == 3:
            try:
                return (float(value[0]), float(value[1]), float(value[2]))
            except (TypeError, ValueError):
                return (0.0, 0.0, 0.0)
        return (0.0, 0.0, 0.0)


sdk_adapter = SDKAdapter()


def get_sdk_adapter() -> SDKAdapter:
    return sdk_adapter


__all__ = [
    "InvalidQuadrupedStateError",
    "QuadrupedMode",
    "QuadrupedPose",
    "QuadrupedTelemetrySnapshot",
    "SDKAdapter",
    "SDKAdapterError",
    "SDKUnavailableError",
    "get_sdk_adapter",
    "sdk_adapter",
]
