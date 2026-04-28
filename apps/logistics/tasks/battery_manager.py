from __future__ import annotations

"""Battery policy and dock-task injection for critical quadruped battery states."""

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from shared.core.config import get_config
from shared.core.event_bus import EventName, get_event_bus
from shared.core.logger import get_logger
from shared.quadruped.robot_registry import RobotNotFoundError, RobotRegistry, get_robot_registry
from shared.quadruped.state_monitor import StateMonitor, get_state_monitor
from apps.logistics.tasks.dispatcher import Dispatcher, get_dispatcher
from apps.logistics.tasks.queue import TaskQueue, get_task_queue


logger = get_logger(__name__)
_LEGACY_ROBOT_ID = "default"
_LOGISTICS_ROLE = "logistics"


class BatteryManagerError(Exception):
    """Raised when battery policy orchestration cannot proceed safely."""


@dataclass(frozen=True)
class BatteryManagerState:
    running: bool
    charging_mode: bool
    dock_task_id: str | None
    dock_task_active: bool
    last_battery_pct: int | None
    last_result: str | None


class BatteryManager:
    """Listens to battery events and injects a dock task when battery is critical."""

    def __init__(
        self,
        task_queue: TaskQueue | None = None,
        dispatcher: Dispatcher | None = None,
        state_monitor: StateMonitor | None = None,
        charging_poll_seconds: int | None = None,
        dock_station_id: str = "DOCK",
        robot_registry: RobotRegistry | None = None,
    ) -> None:
        config = get_config()
        resolved_poll_seconds = (
            config.battery.charging_poll_seconds if charging_poll_seconds is None else charging_poll_seconds
        )
        if not isinstance(resolved_poll_seconds, int) or isinstance(resolved_poll_seconds, bool) or resolved_poll_seconds <= 0:
            raise BatteryManagerError("charging_poll_seconds must be > 0")
        if not isinstance(dock_station_id, str) or not dock_station_id.strip():
            raise BatteryManagerError("dock_station_id must not be empty")

        self._task_queue = task_queue or get_task_queue()
        self._dispatcher = dispatcher or get_dispatcher()
        self._state_monitor = state_monitor or get_state_monitor()
        self._robot_registry = robot_registry or get_robot_registry()
        self._charging_poll_seconds = resolved_poll_seconds
        self._dock_station_id = dock_station_id.strip()

        self._running = False
        self._charging_mode: dict[str, bool] = {}
        self._dock_task_id: dict[str, str] = {}
        self._dock_task_active: dict[str, bool] = {}
        self._dispatcher_paused: dict[str, bool] = {}
        self._last_battery_pct: dict[str, int | None] = {}
        self._last_result: dict[str, str | None] = {}
        self._last_error: str | None = None

        self._subscription_ids: list[str] = []
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._running:
            return
        self._subscribe_events()
        self._running = True
        logger.info("Battery manager started")

    async def stop(self) -> None:
        if not self._running and not self._subscription_ids:
            return
        self._unsubscribe_events()
        self._running = False
        logger.info("Battery manager stopped")

    async def get_state(self) -> BatteryManagerState:
        async with self._lock:
            robot_id = self._legacy_state_robot_id()
            return BatteryManagerState(
                running=self._running,
                charging_mode=self._charging_mode.get(robot_id, False),
                dock_task_id=self._dock_task_id.get(robot_id),
                dock_task_active=self._dock_task_active.get(robot_id, False),
                last_battery_pct=self._last_battery_pct.get(robot_id),
                last_result=self._last_result.get(robot_id),
            )

    def is_running(self) -> bool:
        return self._running

    def in_charging_mode(self) -> bool:
        return self._charging_mode.get(self._legacy_state_robot_id(), False)

    def last_error(self) -> str | None:
        return self._last_error

    async def handle_battery_warn(self, battery_pct: int | None = None, *, robot_id: str | None = None) -> None:
        resolved_robot_id = self._resolve_target_robot_id(robot_id)
        if resolved_robot_id is None:
            return
        async with self._lock:
            self._last_battery_pct[resolved_robot_id] = battery_pct
            self._last_result[resolved_robot_id] = "battery warn handled"
        logger.warning("Battery warning received", extra={"battery_pct": battery_pct, "robot_id": resolved_robot_id})

    async def handle_battery_critical(self, battery_pct: int | None = None, *, robot_id: str | None = None) -> None:
        resolved_robot_id = self._resolve_target_robot_id(robot_id)
        if resolved_robot_id is None:
            return

        async with self._lock:
            self._last_battery_pct[resolved_robot_id] = battery_pct
            if self._charging_mode.get(resolved_robot_id, False):
                self._last_result[resolved_robot_id] = "already in charging mode"
                logger.warning(
                    "Battery critical received while already in charging mode",
                    extra={"robot_id": resolved_robot_id},
                )
                return
            self._charging_mode[resolved_robot_id] = True
            self._dock_task_active[resolved_robot_id] = False
            self._dispatcher_paused[resolved_robot_id] = False
            self._last_result[resolved_robot_id] = "entering charging mode"

        logger.warning(
            "Battery critical received; entering charging mode",
            extra={"battery_pct": battery_pct, "robot_id": resolved_robot_id},
        )

        should_pause_dispatcher = self._should_pause_dispatcher(resolved_robot_id)
        if should_pause_dispatcher:
            try:
                await self._dispatcher.pause(reason="critical battery")
                async with self._lock:
                    self._dispatcher_paused[resolved_robot_id] = True
                logger.info("Dispatcher paused for critical battery dock task")
            except Exception as exc:
                self._record_error(f"Failed to pause dispatcher: {exc}")
                raise BatteryManagerError(f"Failed to pause dispatcher: {exc}") from exc

        try:
            submit_kwargs: dict[str, Any] = {
                "station_id": "CURRENT",
                "destination_id": self._dock_station_id,
                "priority": 9999,
                "notes": f"auto-generated critical battery dock task for {resolved_robot_id}",
            }
            if not (resolved_robot_id == _LEGACY_ROBOT_ID and not self._registered_logistics_robot_ids()):
                submit_kwargs["task_id"] = f"dock-{resolved_robot_id}-{uuid4().hex}"
            task = await self._task_queue.submit_task(
                **submit_kwargs,
            )
        except Exception as exc:
            self._record_error(f"Failed to submit dock task: {exc}")
            async with self._lock:
                self._last_result[resolved_robot_id] = f"dock task submission failed: {exc}"
            logger.exception("Battery manager failed to submit dock task")
            return

        async with self._lock:
            self._dock_task_id[resolved_robot_id] = task.id
            self._dock_task_active[resolved_robot_id] = True
            self._last_result[resolved_robot_id] = "charging mode active"

        if should_pause_dispatcher:
            try:
                await self._dispatcher.resume()
                async with self._lock:
                    self._dispatcher_paused[resolved_robot_id] = False
                logger.info("Dispatcher resumed for dock task dispatch")
            except Exception as exc:
                self._record_error(f"Failed to resume dispatcher: {exc}")
                raise BatteryManagerError(f"Failed to resume dispatcher: {exc}") from exc

    async def handle_battery_recharged(self, battery_pct: int | None = None, *, robot_id: str | None = None) -> None:
        resolved_robot_id = self._resolve_target_robot_id(robot_id)
        if resolved_robot_id is None:
            return

        async with self._lock:
            self._last_battery_pct[resolved_robot_id] = battery_pct
            if not self._charging_mode.get(resolved_robot_id, False):
                self._last_result[resolved_robot_id] = "battery recharged handled"
                return
            dispatcher_should_resume = self._dispatcher_paused.get(resolved_robot_id, False) or self._should_pause_dispatcher(
                resolved_robot_id
            )

        if dispatcher_should_resume:
            try:
                await self._dispatcher.resume()
                logger.info("Dispatcher resumed after recharge")
            except Exception as exc:
                self._record_error(f"Failed to resume dispatcher: {exc}")
                raise BatteryManagerError(f"Failed to resume dispatcher: {exc}") from exc

        async with self._lock:
            self._charging_mode[resolved_robot_id] = False
            self._dock_task_active[resolved_robot_id] = False
            self._dock_task_id.pop(resolved_robot_id, None)
            self._dispatcher_paused[resolved_robot_id] = False
            self._last_result[resolved_robot_id] = "charging mode cleared"

    def _subscribe_events(self) -> None:
        if self._subscription_ids:
            return
        event_bus = get_event_bus()
        self._subscription_ids = [
            event_bus.subscribe(EventName.BATTERY_WARN, self._on_battery_warn),
            event_bus.subscribe(EventName.BATTERY_CRITICAL, self._on_battery_critical),
            event_bus.subscribe(EventName.BATTERY_RECHARGED, self._on_battery_recharged),
        ]

    def _unsubscribe_events(self) -> None:
        if not self._subscription_ids:
            return
        event_bus = get_event_bus()
        for subscription_id in self._subscription_ids:
            event_bus.unsubscribe(subscription_id)
        self._subscription_ids = []

    async def _on_battery_warn(self, event: Any) -> None:
        await self.handle_battery_warn(
            self._extract_battery_pct(event),
            robot_id=self._extract_robot_id(event),
        )

    async def _on_battery_critical(self, event: Any) -> None:
        await self.handle_battery_critical(
            self._extract_battery_pct(event),
            robot_id=self._extract_robot_id(event),
        )

    async def _on_battery_recharged(self, event: Any) -> None:
        await self.handle_battery_recharged(
            self._extract_battery_pct(event),
            robot_id=self._extract_robot_id(event),
        )

    def _extract_battery_pct(self, event: Any) -> int | None:
        battery_pct = event.payload.get("battery_pct") if hasattr(event, "payload") else None
        if isinstance(battery_pct, int) and not isinstance(battery_pct, bool):
            return battery_pct
        return None

    def _extract_robot_id(self, event: Any) -> str | None:
        payload = event.payload if hasattr(event, "payload") and isinstance(event.payload, dict) else {}
        robot_id = payload.get("robot_id")
        if isinstance(robot_id, str) and robot_id:
            return robot_id
        return None

    def _resolve_target_robot_id(self, robot_id: str | None) -> str | None:
        if robot_id is not None:
            if robot_id == _LEGACY_ROBOT_ID and not self._registered_logistics_robot_ids():
                return _LEGACY_ROBOT_ID
            return self._resolve_registered_robot_id(robot_id)

        registered_robot_ids = self._registered_logistics_robot_ids()
        if registered_robot_ids:
            return registered_robot_ids[0]
        return _LEGACY_ROBOT_ID

    def _resolve_registered_robot_id(self, robot_id: str) -> str | None:
        try:
            platform = self._robot_registry.get(robot_id)
        except RobotNotFoundError:
            logger.warning("Battery manager ignored event for unknown robot", extra={"robot_id": robot_id})
            return None

        role = getattr(platform.config, "role", None)
        if role is None:
            role = getattr(platform.config.connection, "role", None)
        if role is not None and role != _LOGISTICS_ROLE:
            logger.warning("Battery manager ignored non-logistics robot", extra={"robot_id": robot_id, "role": role})
            return None
        return robot_id

    def _registered_logistics_robot_ids(self) -> list[str]:
        robot_ids: list[str] = []
        for platform in self._robot_registry.all():
            role = getattr(platform.config, "role", None)
            if role is None:
                role = getattr(platform.config.connection, "role", None)
            if role is not None and role != _LOGISTICS_ROLE:
                continue
            robot_ids.append(platform.robot_id)
        return robot_ids

    def _legacy_state_robot_id(self) -> str:
        registered_robot_ids = self._registered_logistics_robot_ids()
        if registered_robot_ids:
            return registered_robot_ids[0]
        return _LEGACY_ROBOT_ID

    def _should_pause_dispatcher(self, robot_id: str) -> bool:
        return robot_id == _LEGACY_ROBOT_ID and not self._registered_logistics_robot_ids()

    def _record_error(self, message: str) -> None:
        self._last_error = message
        logger.error(message)


battery_manager = BatteryManager()


def get_battery_manager() -> BatteryManager:
    return battery_manager


__all__ = [
    "BatteryManager",
    "BatteryManagerError",
    "BatteryManagerState",
    "battery_manager",
    "get_battery_manager",
]
