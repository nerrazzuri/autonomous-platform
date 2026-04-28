from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from shared.core.config import get_config
from shared.core.event_bus import EventBus, EventName, get_event_bus
from shared.core.logger import get_logger

if TYPE_CHECKING:
    from apps.patrol.tasks.patrol_queue import PatrolQueue


logger = get_logger(__name__)

_UNSET = object()


class PatrolWatchdogError(Exception):
    """Raised when patrol watchdog configuration or lifecycle operations fail."""


@dataclass(frozen=True)
class PatrolWatchdogState:
    running: bool
    suspended: bool
    last_cycle_completed_at: datetime | None
    last_alert_reason: str | None
    loop_iteration: int


def _load_default_patrol_queue() -> "PatrolQueue":
    queue_module = importlib.import_module("apps.patrol.tasks.patrol_queue")
    getter = getattr(queue_module, "get_patrol_queue", None)
    if callable(getter):
        return getter()
    return queue_module.PatrolQueue()


class PatrolWatchdog:
    def __init__(
        self,
        patrol_queue: PatrolQueue | None = None,
        event_bus: EventBus | None = None,
        patrol_interval_seconds: float | None = None,
        loop_interval_seconds: float = 5.0,
    ) -> None:
        config = get_config().patrol
        resolved_patrol_interval = config.patrol_interval_seconds if patrol_interval_seconds is None else patrol_interval_seconds

        if not isinstance(loop_interval_seconds, (int, float)) or float(loop_interval_seconds) <= 0:
            raise PatrolWatchdogError("loop_interval_seconds must be > 0")
        if not isinstance(resolved_patrol_interval, (int, float)) or float(resolved_patrol_interval) <= 0:
            raise PatrolWatchdogError("patrol_interval_seconds must be > 0")

        self._patrol_queue = patrol_queue or _load_default_patrol_queue()
        self._event_bus = event_bus or get_event_bus()
        self._patrol_interval_seconds = float(resolved_patrol_interval)
        self._loop_interval_seconds = float(loop_interval_seconds)

        self._running = False
        self._suspended = False
        self._last_cycle_completed_at: datetime | None = None
        self._last_alert_reason: str | None = None
        self._loop_iteration = 0
        self._last_error: str | None = None

        self._state_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._subscription_ids: list[str] = []

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._running:
                return

            await self._event_bus.start()
            self._subscribe_events()
            self._stop_event.clear()
            await self._set_state(running=True)
            self._task = asyncio.create_task(self._run_loop(), name="patrol-watchdog")
            logger.info("Patrol watchdog started", extra={"loop_interval_seconds": self._loop_interval_seconds})

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if not self._running and self._task is None:
                self._unsubscribe_all()
                return

            await self._set_state(running=False)
            self._stop_event.set()
            task = self._task
            self._task = None
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            self._unsubscribe_all()
            logger.info("Patrol watchdog stopped")

    async def check_once(self) -> bool:
        if self._suspended:
            return True

        now = datetime.now(timezone.utc)
        if self._last_cycle_completed_at is not None:
            stalled_after = timedelta(seconds=self._patrol_interval_seconds * 3)
            if now - self._last_cycle_completed_at > stalled_after:
                return await self._alert_once(
                    reason="patrol_stalled",
                    message="Patrol stalled — no cycle completed in expected window",
                )

        try:
            queue_status = await self._patrol_queue.get_queue_status()
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("Patrol watchdog queue health check failed", extra={"error": str(exc)})
            return True

        scheduled = int(queue_status.get("scheduled", 0) or 0)
        active = int(queue_status.get("active", 0) or 0)
        if scheduled > 0 and active == 0:
            return await self._alert_once(
                reason="patrol_cycles_accumulating",
                message="Patrol cycles accumulating without an active patrol",
            )

        await self._set_state(last_alert_reason=None)
        return True

    async def get_state(self) -> PatrolWatchdogState:
        async with self._state_lock:
            return PatrolWatchdogState(
                running=self._running,
                suspended=self._suspended,
                last_cycle_completed_at=self._last_cycle_completed_at,
                last_alert_reason=self._last_alert_reason,
                loop_iteration=self._loop_iteration,
            )

    def is_running(self) -> bool:
        return self._running

    def last_error(self) -> str | None:
        return self._last_error

    async def _run_loop(self) -> None:
        while self._running:
            await self._increment_loop_iteration()
            try:
                await self.check_once()
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception("Patrol watchdog loop iteration failed")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._loop_interval_seconds)
            except asyncio.TimeoutError:
                continue
            return

    def _subscribe_events(self) -> None:
        if self._subscription_ids:
            return
        self._subscription_ids = [
            self._event_bus.subscribe(
                EventName.PATROL_CYCLE_COMPLETED,
                self._handle_cycle_completed,
                subscriber_name="patrol-watchdog",
            ),
            self._event_bus.subscribe(
                EventName.PATROL_SUSPENDED,
                self._handle_patrol_suspended,
                subscriber_name="patrol-watchdog",
            ),
            self._event_bus.subscribe(
                EventName.PATROL_RESUMED,
                self._handle_patrol_resumed,
                subscriber_name="patrol-watchdog",
            ),
        ]

    def _unsubscribe_all(self) -> None:
        for subscription_id in self._subscription_ids:
            self._event_bus.unsubscribe(subscription_id)
        self._subscription_ids = []

    async def _handle_cycle_completed(self, _event: Any) -> None:
        await self._set_state(last_cycle_completed_at=datetime.now(timezone.utc), last_alert_reason=None)

    async def _handle_patrol_suspended(self, _event: Any) -> None:
        await self._set_state(suspended=True)

    async def _handle_patrol_resumed(self, _event: Any) -> None:
        await self._set_state(suspended=False, last_alert_reason=None)

    async def _alert_once(self, *, reason: str, message: str) -> bool:
        if self._last_alert_reason == reason:
            return False

        await self._publish_event(
            EventName.SYSTEM_ALERT,
            {
                "severity": "warning",
                "reason": reason,
                "message": message,
                "module": "patrol_watchdog",
            },
        )
        await self._set_state(last_alert_reason=reason)
        return False

    async def _publish_event(self, event_name: EventName, payload: dict[str, Any]) -> None:
        try:
            await self._event_bus.publish(event_name, payload=payload, source=__name__)
        except Exception:
            logger.warning("Patrol watchdog event publish skipped", extra={"event_name": event_name.value})

    async def _set_state(
        self,
        *,
        running: bool | object = _UNSET,
        suspended: bool | object = _UNSET,
        last_cycle_completed_at: datetime | None | object = _UNSET,
        last_alert_reason: str | None | object = _UNSET,
        loop_iteration: int | object = _UNSET,
    ) -> None:
        async with self._state_lock:
            if running is not _UNSET:
                self._running = bool(running)
            if suspended is not _UNSET:
                self._suspended = bool(suspended)
            if last_cycle_completed_at is not _UNSET:
                self._last_cycle_completed_at = last_cycle_completed_at
            if last_alert_reason is not _UNSET:
                self._last_alert_reason = last_alert_reason
            if loop_iteration is not _UNSET:
                self._loop_iteration = int(loop_iteration)

    async def _increment_loop_iteration(self) -> None:
        async with self._state_lock:
            self._loop_iteration += 1


patrol_watchdog = PatrolWatchdog()


def get_patrol_watchdog() -> PatrolWatchdog:
    return patrol_watchdog


__all__ = [
    "PatrolWatchdog",
    "PatrolWatchdogError",
    "PatrolWatchdogState",
    "get_patrol_watchdog",
    "patrol_watchdog",
]
