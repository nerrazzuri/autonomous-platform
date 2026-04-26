from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass

from shared.core.config import get_config
from shared.core.event_bus import EventBus, EventName, get_event_bus
from shared.core.logger import get_logger

from apps.patrol.tasks.patrol_queue import PatrolQueue


logger = get_logger(__name__)


class PatrolSchedulerError(Exception):
    """Raised when patrol scheduler configuration or lifecycle operations fail."""


@dataclass(frozen=True)
class PatrolSchedulerState:
    running: bool
    suspended: bool
    charging_inhibited: bool
    last_cycle_id: str | None
    last_result: str | None
    loop_iteration: int


def _load_default_patrol_queue() -> PatrolQueue:
    queue_module = importlib.import_module("apps.patrol.tasks.patrol_queue")
    getter = getattr(queue_module, "get_patrol_queue", None)
    if callable(getter):
        return getter()
    return PatrolQueue()


class PatrolScheduler:
    def __init__(
        self,
        patrol_queue: PatrolQueue | None = None,
        event_bus: EventBus | None = None,
        schedule_enabled: bool | None = None,
        patrol_interval_seconds: float | None = None,
        default_route_id: str | None = None,
    ) -> None:
        config = get_config().patrol
        resolved_interval = config.patrol_interval_seconds if patrol_interval_seconds is None else patrol_interval_seconds
        resolved_route = getattr(config, "default_route_id", "PATROL_NORTH_LOOP") if default_route_id is None else default_route_id

        if not isinstance(resolved_interval, (int, float)) or float(resolved_interval) <= 0:
            raise PatrolSchedulerError("patrol_interval_seconds must be > 0")
        if not isinstance(resolved_route, str) or not resolved_route.strip():
            raise PatrolSchedulerError("default_route_id must not be empty")

        self._patrol_queue = patrol_queue or _load_default_patrol_queue()
        self._event_bus = event_bus or get_event_bus()
        self._schedule_enabled = config.schedule_enabled if schedule_enabled is None else bool(schedule_enabled)
        self._patrol_interval_seconds = float(resolved_interval)
        self._default_route_id = resolved_route.strip()

        self._state = PatrolSchedulerState(
            running=False,
            suspended=False,
            charging_inhibited=False,
            last_cycle_id=None,
            last_result=None,
            loop_iteration=0,
        )
        self._state_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._subscription_ids: list[str] = []
        self._last_error: str | None = None

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._state.running:
                return

            await self._event_bus.start()
            self._subscribe_events()
            self._stop_event.clear()
            await self._set_state(running=True)
            self._task = asyncio.create_task(self._run_loop(), name="patrol-scheduler")
            logger.info(
                "Patrol scheduler started",
                extra={"default_route_id": self._default_route_id, "interval_seconds": self._patrol_interval_seconds},
            )

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if not self._state.running and self._task is None:
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
            logger.info("Patrol scheduler stopped")

    async def run_once(self) -> bool:
        try:
            state = await self.get_state()
            if not self._schedule_enabled:
                await self._set_result("schedule disabled")
                return False
            if state.suspended:
                await self._set_result("scheduler suspended")
                return False
            if state.charging_inhibited:
                await self._set_result("charging inhibited")
                return False

            queue_status = await self._patrol_queue.get_queue_status()
            active_count = int(queue_status.get("active", 0) or 0)
            scheduled_count = int(queue_status.get("scheduled", 0) or 0)
            if active_count > 0:
                await self._set_result("active cycle exists")
                return False
            if scheduled_count > 0:
                await self._set_result("scheduled cycle exists")
                return False

            cycle = await self._patrol_queue.submit_cycle(route_id=self._default_route_id, triggered_by="schedule")
            await self._set_result("scheduled", last_cycle_id=cycle.cycle_id)
            self._publish_event(
                EventName.PATROL_CYCLE_STARTED,
                {
                    "cycle_id": cycle.cycle_id,
                    "route_id": cycle.route_id,
                    "triggered_by": cycle.triggered_by,
                    "status": cycle.status,
                },
                task_id=cycle.cycle_id,
            )
            return True
        except Exception as exc:
            self._last_error = str(exc)
            await self._set_result(str(exc))
            logger.exception("Patrol scheduler run_once failed")
            return False

    async def get_state(self) -> PatrolSchedulerState:
        async with self._state_lock:
            return PatrolSchedulerState(**self._state.__dict__)

    async def suspend(self, reason: str = "suspended") -> None:
        if not isinstance(reason, str) or not reason.strip():
            reason = "suspended"
        state = await self.get_state()
        if state.suspended:
            return
        await self._set_state(suspended=True)
        self._publish_event(EventName.PATROL_SUSPENDED, {"reason": reason.strip()})

    async def resume(self, reason: str = "resumed") -> None:
        if not isinstance(reason, str) or not reason.strip():
            reason = "resumed"
        state = await self.get_state()
        if not state.suspended:
            return
        await self._set_state(suspended=False)
        self._publish_event(EventName.PATROL_RESUMED, {"reason": reason.strip()})

    def is_running(self) -> bool:
        return self._state.running

    def last_error(self) -> str | None:
        return self._last_error

    async def _run_loop(self) -> None:
        while True:
            state = await self.get_state()
            if not state.running:
                return

            await self._increment_loop_iteration()
            try:
                await self.run_once()
            except Exception as exc:
                self._last_error = str(exc)
                await self._set_result(str(exc))
                logger.exception("Patrol scheduler loop iteration failed")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._patrol_interval_seconds)
            except asyncio.TimeoutError:
                continue
            return

    def _subscribe_events(self) -> None:
        if self._subscription_ids:
            return
        self._subscription_ids = [
            self._event_bus.subscribe(EventName.BATTERY_CRITICAL, self._handle_battery_critical, subscriber_name="patrol-scheduler"),
            self._event_bus.subscribe(EventName.BATTERY_RECHARGED, self._handle_battery_recharged, subscriber_name="patrol-scheduler"),
            self._event_bus.subscribe(EventName.PATROL_SUSPENDED, self._handle_patrol_suspended, subscriber_name="patrol-scheduler"),
            self._event_bus.subscribe(EventName.PATROL_RESUMED, self._handle_patrol_resumed, subscriber_name="patrol-scheduler"),
        ]

    def _unsubscribe_all(self) -> None:
        for subscription_id in self._subscription_ids:
            self._event_bus.unsubscribe(subscription_id)
        self._subscription_ids = []

    async def _handle_battery_critical(self, _event) -> None:
        await self._set_state(charging_inhibited=True)

    async def _handle_battery_recharged(self, _event) -> None:
        await self._set_state(charging_inhibited=False)

    async def _handle_patrol_suspended(self, _event) -> None:
        await self._set_state(suspended=True)

    async def _handle_patrol_resumed(self, _event) -> None:
        await self._set_state(suspended=False)

    async def _set_state(
        self,
        *,
        running: bool | None = None,
        suspended: bool | None = None,
        charging_inhibited: bool | None = None,
        last_cycle_id: str | None = None,
        last_result: str | None = None,
        loop_iteration: int | None = None,
    ) -> None:
        async with self._state_lock:
            self._state = PatrolSchedulerState(
                running=self._state.running if running is None else running,
                suspended=self._state.suspended if suspended is None else suspended,
                charging_inhibited=self._state.charging_inhibited if charging_inhibited is None else charging_inhibited,
                last_cycle_id=self._state.last_cycle_id if last_cycle_id is None else last_cycle_id,
                last_result=self._state.last_result if last_result is None else last_result,
                loop_iteration=self._state.loop_iteration if loop_iteration is None else loop_iteration,
            )

    async def _set_result(self, result: str, *, last_cycle_id: str | None = None) -> None:
        async with self._state_lock:
            self._state = PatrolSchedulerState(
                running=self._state.running,
                suspended=self._state.suspended,
                charging_inhibited=self._state.charging_inhibited,
                last_cycle_id=self._state.last_cycle_id if last_cycle_id is None else last_cycle_id,
                last_result=result,
                loop_iteration=self._state.loop_iteration,
            )

    async def _increment_loop_iteration(self) -> None:
        async with self._state_lock:
            self._state = PatrolSchedulerState(
                running=self._state.running,
                suspended=self._state.suspended,
                charging_inhibited=self._state.charging_inhibited,
                last_cycle_id=self._state.last_cycle_id,
                last_result=self._state.last_result,
                loop_iteration=self._state.loop_iteration + 1,
            )

    def _publish_event(self, event_name: EventName, payload: dict[str, object], task_id: str | None = None) -> None:
        try:
            self._event_bus.publish_nowait(event_name, payload, source=__name__, task_id=task_id)
        except Exception:
            logger.debug("Patrol scheduler event publish skipped", extra={"event_name": event_name.value})


patrol_scheduler = PatrolScheduler()


def get_patrol_scheduler() -> PatrolScheduler:
    return patrol_scheduler


__all__ = [
    "PatrolScheduler",
    "PatrolSchedulerError",
    "PatrolSchedulerState",
    "get_patrol_scheduler",
    "patrol_scheduler",
]
