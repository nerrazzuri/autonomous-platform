from __future__ import annotations

"""Battery policy and dock-task injection for critical quadruped battery states."""

import asyncio
from dataclasses import dataclass
from typing import Any

from shared.core.config import get_config
from shared.core.event_bus import EventName, get_event_bus
from shared.core.logger import get_logger
from shared.quadruped.state_monitor import StateMonitor, get_state_monitor
from apps.logistics.tasks.dispatcher import Dispatcher, get_dispatcher
from apps.logistics.tasks.queue import TaskQueue, get_task_queue


logger = get_logger(__name__)


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
        self._charging_poll_seconds = resolved_poll_seconds
        self._dock_station_id = dock_station_id.strip()

        self._running = False
        self._charging_mode = False
        self._dock_task_id: str | None = None
        self._dock_task_active = False
        self._last_battery_pct: int | None = None
        self._last_result: str | None = None
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
            return BatteryManagerState(
                running=self._running,
                charging_mode=self._charging_mode,
                dock_task_id=self._dock_task_id,
                dock_task_active=self._dock_task_active,
                last_battery_pct=self._last_battery_pct,
                last_result=self._last_result,
            )

    def is_running(self) -> bool:
        return self._running

    def in_charging_mode(self) -> bool:
        return self._charging_mode

    def last_error(self) -> str | None:
        return self._last_error

    async def handle_battery_warn(self, battery_pct: int | None = None) -> None:
        async with self._lock:
            self._last_battery_pct = battery_pct
            self._last_result = "battery warn handled"
        logger.warning("Battery warning received", extra={"battery_pct": battery_pct})

    async def handle_battery_critical(self, battery_pct: int | None = None) -> None:
        async with self._lock:
            self._last_battery_pct = battery_pct
            if self._charging_mode:
                self._last_result = "already in charging mode"
                logger.warning("Battery critical received while already in charging mode")
                return
            self._charging_mode = True
            self._dock_task_active = False
            self._last_result = "entering charging mode"

        logger.warning("Battery critical received; entering charging mode", extra={"battery_pct": battery_pct})

        try:
            await self._dispatcher.pause(reason="critical battery")
            logger.info("Dispatcher paused for critical battery dock task")
        except Exception as exc:
            self._record_error(f"Failed to pause dispatcher: {exc}")
            raise BatteryManagerError(f"Failed to pause dispatcher: {exc}") from exc

        try:
            task = await self._task_queue.submit_task(
                station_id="CURRENT",
                destination_id=self._dock_station_id,
                priority=9999,
                notes="auto-generated critical battery dock task",
            )
        except Exception as exc:
            self._record_error(f"Failed to submit dock task: {exc}")
            async with self._lock:
                self._last_result = f"dock task submission failed: {exc}"
            logger.exception("Battery manager failed to submit dock task")
            return

        async with self._lock:
            self._dock_task_id = task.id
            self._dock_task_active = True
            self._last_result = "charging mode active"

        try:
            await self._dispatcher.resume()
            logger.info("Dispatcher resumed for dock task dispatch")
        except Exception as exc:
            self._record_error(f"Failed to resume dispatcher: {exc}")
            raise BatteryManagerError(f"Failed to resume dispatcher: {exc}") from exc

    async def handle_battery_recharged(self, battery_pct: int | None = None) -> None:
        async with self._lock:
            self._last_battery_pct = battery_pct
            if not self._charging_mode:
                self._last_result = "battery recharged handled"
                return

        try:
            await self._dispatcher.resume()
            logger.info("Dispatcher resumed after recharge")
        except Exception as exc:
            self._record_error(f"Failed to resume dispatcher: {exc}")
            raise BatteryManagerError(f"Failed to resume dispatcher: {exc}") from exc

        async with self._lock:
            self._charging_mode = False
            self._dock_task_active = False
            self._dock_task_id = None
            self._last_result = "charging mode cleared"

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
        await self.handle_battery_warn(self._extract_battery_pct(event))

    async def _on_battery_critical(self, event: Any) -> None:
        await self.handle_battery_critical(self._extract_battery_pct(event))

    async def _on_battery_recharged(self, event: Any) -> None:
        await self.handle_battery_recharged(self._extract_battery_pct(event))

    def _extract_battery_pct(self, event: Any) -> int | None:
        battery_pct = event.payload.get("battery_pct") if hasattr(event, "payload") else None
        if isinstance(battery_pct, int) and not isinstance(battery_pct, bool):
            return battery_pct
        return None

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
