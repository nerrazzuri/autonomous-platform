from __future__ import annotations

"""Watchdog and liveness monitoring for quadruped telemetry and active tasks."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.event_bus import EventName, get_event_bus
from core.logger import get_logger
from quadruped.state_monitor import StateMonitor, get_state_monitor
from tasks.dispatcher import Dispatcher, get_dispatcher
from tasks.queue import TaskQueue, get_task_queue


logger = get_logger(__name__)


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
    ) -> None:
        if telemetry_timeout_seconds <= 0:
            raise WatchdogError("telemetry_timeout_seconds must be > 0")
        if loop_interval_seconds <= 0:
            raise WatchdogError("loop_interval_seconds must be > 0")

        self._state_monitor = state_monitor or get_state_monitor()
        self._dispatcher = dispatcher or get_dispatcher()
        self._task_queue = task_queue or get_task_queue()
        self._telemetry_timeout_seconds = telemetry_timeout_seconds
        self._loop_interval_seconds = loop_interval_seconds

        self._running = False
        self._last_telemetry_at: datetime | None = None
        self._last_connection_ok: bool | None = None
        self._alert_active = False
        self._last_alert_reason: str | None = None
        self._last_result: str | None = None
        self._last_error: str | None = None

        self._subscription_ids: list[str] = []
        self._loop_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_handled_interruption: tuple[str | None, str | None] | None = None
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
        async with self._state_lock:
            last_telemetry_at = self._last_telemetry_at
            last_connection_ok = self._last_connection_ok

        if last_telemetry_at is None:
            return True

        now = datetime.now(timezone.utc)
        if (now - last_telemetry_at).total_seconds() > self._telemetry_timeout_seconds:
            await self._set_alert("telemetry_timeout")
            await self._handle_active_task_interruption("telemetry_timeout")
            self._emit_alert("telemetry_timeout", severity="critical")
            return False

        if last_connection_ok is False:
            reason = await self._classify_connection_loss_reason()
            await self._set_alert(reason)
            await self._handle_active_task_interruption(reason)
            self._emit_alert(reason, severity="critical")
            return False

        return True

    async def get_state(self) -> WatchdogState:
        async with self._state_lock:
            return WatchdogState(
                running=self._running,
                last_telemetry_at=self._last_telemetry_at,
                last_connection_ok=self._last_connection_ok,
                alert_active=self._alert_active,
                last_alert_reason=self._last_alert_reason,
                last_result=self._last_result,
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
        ]

    def _unsubscribe_events(self) -> None:
        if not self._subscription_ids:
            return
        event_bus = get_event_bus()
        for subscription_id in self._subscription_ids:
            event_bus.unsubscribe(subscription_id)
        self._subscription_ids = []

    async def _on_telemetry(self, event: Any) -> None:
        connection_ok = event.payload.get("connection_ok")
        async with self._state_lock:
            self._last_telemetry_at = datetime.now(timezone.utc)
            if isinstance(connection_ok, bool):
                self._last_connection_ok = connection_ok

        if self._alert_active and self._last_connection_ok is True:
            await self._clear_alert_if_recovered()

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

    async def _classify_connection_loss_reason(self) -> str:
        state = await self._state_monitor.get_current_state()
        if state is not None and state.connection_ok is False and state.battery_pct == 0:
            return "quadruped_power_loss"
        return "connection_lost"

    async def _set_alert(self, reason: str) -> None:
        async with self._state_lock:
            self._alert_active = True
            self._last_alert_reason = reason
            self._last_result = reason

    async def _handle_active_task_interruption(self, reason: str) -> None:
        dispatcher_state = await self._dispatcher.get_state()
        active_task_id = dispatcher_state.active_task_id
        task_key = (active_task_id, reason)
        if self._last_handled_interruption == task_key:
            return

        self._last_handled_interruption = task_key
        if active_task_id is None:
            return

        try:
            await self._task_queue.mark_failed(active_task_id, notes=reason)
            async with self._state_lock:
                self._last_result = f"failed_active_task:{active_task_id}"
            logger.warning("Watchdog marked active task failed", extra={"task_id": active_task_id, "reason": reason})
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning(
                "Watchdog could not mark active task failed",
                extra={"task_id": active_task_id, "reason": reason},
            )

    def _emit_alert(self, reason: str, severity: str = "critical", active_task_id: str | None = None) -> None:
        try:
            get_event_bus().publish_nowait(
                EventName.SYSTEM_ALERT,
                payload={
                    "severity": severity,
                    "reason": reason,
                    "active_task_id": active_task_id,
                    "module": "watchdog",
                },
                source=__name__,
            )
        except Exception:
            logger.debug("Watchdog alert publish skipped", extra={"reason": reason, "severity": severity})

    async def _clear_alert_if_recovered(self) -> None:
        async with self._state_lock:
            self._alert_active = False
            self._last_alert_reason = None
            self._last_result = "telemetry_restored"
            self._last_handled_interruption = None
        self._emit_alert("telemetry_restored", severity="info")
        logger.info("Watchdog telemetry restored after alert")


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
