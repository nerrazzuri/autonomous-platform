from __future__ import annotations

"""Watchdog and liveness monitoring for quadruped telemetry and active tasks."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from shared.core.event_bus import EventName, get_event_bus
from shared.core.logger import get_logger
from shared.quadruped.robot_registry import RobotNotFoundError, RobotRegistry, get_robot_registry
from shared.quadruped.state_monitor import StateMonitor, get_state_monitor
from apps.logistics.tasks.dispatcher import Dispatcher, get_dispatcher
from apps.logistics.tasks.queue import TaskQueue, get_task_queue


logger = get_logger(__name__)
_LEGACY_ROBOT_ID = "default"
_LOGISTICS_ROLE = "logistics"


class WatchdogError(Exception):
    """Raised when watchdog configuration is invalid or monitoring fails critically."""


@dataclass(frozen=True)
class WatchdogState:
    running: bool
    last_telemetry_at: datetime | None
    last_connection_ok: bool | None
    alert_active: bool
    last_alert_reason: str | None
    last_result: str | None


class Watchdog:
    """Monitors telemetry freshness and marks active tasks failed on likely loss events."""

    def __init__(
        self,
        state_monitor: StateMonitor | None = None,
        dispatcher: Dispatcher | None = None,
        task_queue: TaskQueue | None = None,
        telemetry_timeout_seconds: float = 5.0,
        loop_interval_seconds: float = 1.0,
        robot_registry: RobotRegistry | None = None,
    ) -> None:
        if telemetry_timeout_seconds <= 0:
            raise WatchdogError("telemetry_timeout_seconds must be > 0")
        if loop_interval_seconds <= 0:
            raise WatchdogError("loop_interval_seconds must be > 0")

        self._state_monitor = state_monitor or get_state_monitor()
        self._dispatcher = dispatcher or get_dispatcher()
        self._task_queue = task_queue or get_task_queue()
        self._robot_registry = robot_registry or get_robot_registry()
        self._telemetry_timeout_seconds = telemetry_timeout_seconds
        self._loop_interval_seconds = loop_interval_seconds

        self._running = False
        self._last_telemetry_at: datetime | None = None
        self._last_connection_ok: bool | None = None
        self._alert_active = False
        self._last_alert_reason: str | None = None
        self._last_result: str | None = None
        self._last_error: str | None = None
        self._last_telemetry_at_by_robot: dict[str, datetime | None] = {}
        self._last_connection_ok_by_robot: dict[str, bool | None] = {}
        self._alert_active_by_robot: dict[str, bool] = {}
        self._last_alert_reason_by_robot: dict[str, str | None] = {}
        self._last_result_by_robot: dict[str, str | None] = {}

        self._subscription_ids: list[str] = []
        self._loop_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_handled_interruption: tuple[str | None, str | None] | None = None
        self._last_handled_interruption_by_robot: dict[str, tuple[str | None, str | None] | None] = {}
        self._state_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return

        self._subscribe_events()
        self._running = True
        self._stop_event = asyncio.Event()
        self._loop_task = asyncio.create_task(self._run_loop(), name="sumitomo-watchdog")
        logger.info("Watchdog started")

    async def stop(self) -> None:
        if self._loop_task is None:
            self._running = False
            self._unsubscribe_events()
            return
        if self._loop_task.done():
            self._running = False
            self._loop_task = None
            self._unsubscribe_events()
            return

        self._running = False
        self._stop_event.set()
        try:
            await self._loop_task
        finally:
            self._loop_task = None
            self._unsubscribe_events()
            logger.info("Watchdog stopped")

    async def check_once(self) -> bool:
        robot_ids = self._registered_logistics_robot_ids()
        if not robot_ids:
            robot_ids = [_LEGACY_ROBOT_ID]

        all_healthy = True
        for robot_id in robot_ids:
            if not await self._check_robot_once(robot_id):
                all_healthy = False
        return all_healthy

    async def get_state(self) -> WatchdogState:
        async with self._state_lock:
            robot_id = self._legacy_state_robot_id()
            return WatchdogState(
                running=self._running,
                last_telemetry_at=self._last_telemetry_at_by_robot.get(robot_id, self._last_telemetry_at),
                last_connection_ok=self._last_connection_ok_by_robot.get(robot_id, self._last_connection_ok),
                alert_active=self._alert_active_by_robot.get(robot_id, self._alert_active),
                last_alert_reason=self._last_alert_reason_by_robot.get(robot_id, self._last_alert_reason),
                last_result=self._last_result_by_robot.get(robot_id, self._last_result),
            )

    def is_running(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    def last_error(self) -> str | None:
        return self._last_error

    def _subscribe_events(self) -> None:
        if self._subscription_ids:
            return
        event_bus = get_event_bus()
        self._subscription_ids = [
            event_bus.subscribe(EventName.QUADRUPED_TELEMETRY, self._on_telemetry),
            event_bus.subscribe(EventName.QUADRUPED_CONNECTION_LOST, self._on_connection_lost),
            event_bus.subscribe(EventName.QUADRUPED_CONNECTION_RESTORED, self._on_connection_restored),
        ]

    def _unsubscribe_events(self) -> None:
        if not self._subscription_ids:
            return
        event_bus = get_event_bus()
        for subscription_id in self._subscription_ids:
            event_bus.unsubscribe(subscription_id)
        self._subscription_ids = []

    async def _on_telemetry(self, event: Any) -> None:
        robot_id = self._resolve_target_robot_id(self._extract_robot_id(event))
        if robot_id is None:
            return
        connection_ok = event.payload.get("connection_ok")
        async with self._state_lock:
            self._last_telemetry_at_by_robot[robot_id] = datetime.now(timezone.utc)
            if isinstance(connection_ok, bool):
                self._last_connection_ok_by_robot[robot_id] = connection_ok
            self._sync_legacy_state_unlocked(robot_id)

        if self._alert_active_by_robot.get(robot_id, self._alert_active if robot_id == self._legacy_state_robot_id() else False) and self._last_connection_ok_by_robot.get(robot_id) is True:
            await self._clear_alert_if_recovered(robot_id)

    async def _on_connection_lost(self, event: Any) -> None:
        robot_id = self._resolve_target_robot_id(self._extract_robot_id(event))
        if robot_id is None:
            return
        async with self._state_lock:
            self._last_connection_ok_by_robot[robot_id] = False
            self._sync_legacy_state_unlocked(robot_id)
        logger.warning("Watchdog observed connection lost", extra={"robot_id": robot_id, "status": "connection_lost"})

    async def _on_connection_restored(self, event: Any) -> None:
        robot_id = self._resolve_target_robot_id(self._extract_robot_id(event))
        if robot_id is None:
            return
        async with self._state_lock:
            self._last_connection_ok_by_robot[robot_id] = True
            self._last_telemetry_at_by_robot[robot_id] = datetime.now(timezone.utc)
            self._sync_legacy_state_unlocked(robot_id)
        logger.info("Watchdog observed connection restored", extra={"robot_id": robot_id, "status": "connection_restored"})
        if self._alert_active_by_robot.get(robot_id, self._alert_active if robot_id == self._legacy_state_robot_id() else False):
            await self._clear_alert_if_recovered(robot_id)

    async def _run_loop(self) -> None:
        while self._running and not self._stop_event.is_set():
            try:
                await self.check_once()
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception("Watchdog loop failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._loop_interval_seconds)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def _check_robot_once(self, robot_id: str) -> bool:
        async with self._state_lock:
            last_telemetry_at = self._last_telemetry_at_by_robot.get(robot_id)
            if last_telemetry_at is None and robot_id == self._legacy_state_robot_id():
                last_telemetry_at = self._last_telemetry_at

            last_connection_ok = self._last_connection_ok_by_robot.get(robot_id)
            if last_connection_ok is None and robot_id == self._legacy_state_robot_id():
                last_connection_ok = self._last_connection_ok

        if last_telemetry_at is None:
            return True

        now = datetime.now(timezone.utc)
        if (now - last_telemetry_at).total_seconds() > self._telemetry_timeout_seconds:
            logger.warning("Watchdog detected stale telemetry", extra={"robot_id": robot_id, "status": "telemetry_timeout"})
            await self._set_alert(robot_id, "telemetry_timeout")
            await self._handle_active_task_interruption(robot_id, "telemetry_timeout")
            self._emit_alert("telemetry_timeout", severity="critical", robot_id=robot_id)
            return False

        if last_connection_ok is False:
            reason = await self._classify_connection_loss_reason(robot_id)
            logger.warning("Watchdog detected connection fault", extra={"robot_id": robot_id, "status": reason})
            await self._set_alert(robot_id, reason)
            await self._handle_active_task_interruption(robot_id, reason)
            self._emit_alert(reason, severity="critical", robot_id=robot_id)
            return False

        return True

    async def _classify_connection_loss_reason(self, robot_id: str) -> str:
        state_monitor = self._resolve_state_monitor_for_robot(robot_id)
        state = await state_monitor.get_current_state()
        if state is not None and state.connection_ok is False and state.battery_pct == 0:
            return "quadruped_power_loss"
        return "connection_lost"

    async def _set_alert(self, robot_id: str, reason: str) -> None:
        async with self._state_lock:
            self._alert_active_by_robot[robot_id] = True
            self._last_alert_reason_by_robot[robot_id] = reason
            self._last_result_by_robot[robot_id] = reason
            self._sync_legacy_state_unlocked(robot_id)

    async def _handle_active_task_interruption(self, robot_id: str, reason: str) -> None:
        active_task_id = await self._resolve_active_task_id(robot_id)
        task_key = (active_task_id, reason)
        last_handled = self._last_handled_interruption_by_robot.get(
            robot_id,
            self._last_handled_interruption if robot_id == self._legacy_state_robot_id() else None,
        )
        if last_handled == task_key:
            return

        self._last_handled_interruption_by_robot[robot_id] = task_key
        if robot_id == self._legacy_state_robot_id():
            self._last_handled_interruption = task_key
        if active_task_id is None:
            return

        try:
            await self._task_queue.mark_failed(active_task_id, notes=reason)
            async with self._state_lock:
                self._last_result_by_robot[robot_id] = f"failed_active_task:{active_task_id}"
                self._sync_legacy_state_unlocked(robot_id)
            logger.warning(
                "Watchdog marked active task failed",
                extra={"task_id": active_task_id, "reason": reason, "robot_id": robot_id},
            )
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning(
                "Watchdog could not mark active task failed",
                extra={"task_id": active_task_id, "reason": reason, "robot_id": robot_id},
            )

    def _emit_alert(
        self,
        reason: str,
        severity: str = "critical",
        active_task_id: str | None = None,
        robot_id: str | None = None,
    ) -> None:
        try:
            payload = {
                "severity": severity,
                "reason": reason,
                "active_task_id": active_task_id,
                "module": "watchdog",
            }
            if robot_id is not None:
                payload["robot_id"] = robot_id
            get_event_bus().publish_nowait(
                EventName.SYSTEM_ALERT,
                payload=payload,
                source=__name__,
            )
        except Exception:
            logger.debug("Watchdog alert publish skipped", extra={"reason": reason, "severity": severity})
            return
        logger.warning(
            "Watchdog emitted alert",
            extra={"robot_id": robot_id, "status": reason, "event_type": "system_alert", "severity": severity},
        )

    async def _clear_alert_if_recovered(self, robot_id: str) -> None:
        async with self._state_lock:
            self._alert_active_by_robot[robot_id] = False
            self._last_alert_reason_by_robot[robot_id] = None
            self._last_result_by_robot[robot_id] = "telemetry_restored"
            self._last_handled_interruption_by_robot[robot_id] = None
            if robot_id == self._legacy_state_robot_id():
                self._last_handled_interruption = None
            self._sync_legacy_state_unlocked(robot_id)
        self._emit_alert("telemetry_restored", severity="info", robot_id=robot_id)
        logger.info("Watchdog telemetry restored after alert", extra={"robot_id": robot_id})

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
            logger.warning("Watchdog ignored event for unknown robot", extra={"robot_id": robot_id})
            return None

        role = getattr(platform.config, "role", None)
        if role is None:
            role = getattr(platform.config.connection, "role", None)
        if role is not None and role != _LOGISTICS_ROLE:
            logger.warning("Watchdog ignored non-logistics robot", extra={"robot_id": robot_id, "role": role})
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

    def _sync_legacy_state_unlocked(self, robot_id: str) -> None:
        if robot_id != self._legacy_state_robot_id():
            return
        self._last_telemetry_at = self._last_telemetry_at_by_robot.get(robot_id)
        self._last_connection_ok = self._last_connection_ok_by_robot.get(robot_id)
        self._alert_active = self._alert_active_by_robot.get(robot_id, False)
        self._last_alert_reason = self._last_alert_reason_by_robot.get(robot_id)
        self._last_result = self._last_result_by_robot.get(robot_id)

    def _resolve_state_monitor_for_robot(self, robot_id: str) -> StateMonitor:
        if robot_id == _LEGACY_ROBOT_ID and not self._registered_logistics_robot_ids():
            return self._state_monitor
        try:
            return self._robot_registry.get(robot_id).state_monitor
        except RobotNotFoundError:
            return self._state_monitor

    async def _resolve_active_task_id(self, robot_id: str) -> str | None:
        active_tasks = getattr(self._dispatcher, "_active_tasks", None)
        if isinstance(active_tasks, dict):
            if robot_id in active_tasks:
                return active_tasks.get(robot_id)
            if robot_id != self._legacy_state_robot_id():
                return None

        if robot_id != self._legacy_state_robot_id():
            return None

        dispatcher_state = await self._dispatcher.get_state()
        return dispatcher_state.active_task_id


watchdog = Watchdog()


def get_watchdog() -> Watchdog:
    return watchdog


__all__ = [
    "Watchdog",
    "WatchdogError",
    "WatchdogState",
    "get_watchdog",
    "watchdog",
]
