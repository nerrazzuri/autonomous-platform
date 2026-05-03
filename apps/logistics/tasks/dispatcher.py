from __future__ import annotations

"""Thin orchestration layer between queued tasks and navigation execution."""

import asyncio
from dataclasses import dataclass
from typing import Any

from shared.core.event_bus import EventName, get_event_bus
from shared.core.logger import get_logger
from shared.navigation.navigator import Navigator, NavigationResult, get_navigator
from shared.quadruped.robot_registry import RobotNotFoundError, RobotRegistry, get_robot_registry
from shared.quadruped.state_monitor import StateMonitor, get_state_monitor
from apps.logistics.tasks.queue import TaskQueue, get_task_queue


logger = get_logger(__name__)

TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
_LEGACY_ROBOT_ID = "default"
_LOGISTICS_ROLE = "logistics"


class DispatcherError(Exception):
    """Raised when dispatcher configuration or orchestration fails."""


@dataclass(frozen=True)
class DispatchState:
    running: bool
    paused: bool
    active_task_id: str | None
    active_route_origin: str | None
    active_route_destination: str | None
    last_result: str | None
    loop_iteration: int


class Dispatcher:
    """Coordinates queued task execution through the navigator."""

    def __init__(
        self,
        task_queue: TaskQueue | None = None,
        navigator: Navigator | None = None,
        state_monitor: StateMonitor | None = None,
        poll_interval_seconds: float = 0.2,
        robot_registry: RobotRegistry | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise DispatcherError("poll_interval_seconds must be > 0")

        self._task_queue = task_queue or get_task_queue()
        self._navigator = navigator or get_navigator()
        self._state_monitor = state_monitor or get_state_monitor()
        self._robot_registry = robot_registry or get_robot_registry()
        self._poll_interval_seconds = poll_interval_seconds

        self._running = False
        self._paused = False
        self._active_task_id: str | None = None
        self._active_route_origin: str | None = None
        self._active_route_destination: str | None = None
        self._last_result: str | None = None
        self._loop_iteration = 0
        self._last_error: str | None = None

        self._arrived_hold_count = 0
        self._load_confirmed = False
        self._unload_confirmed = False
        self._active_tasks: dict[str, str] = {}
        self._active_route_origins: dict[str, str] = {}
        self._active_route_destinations: dict[str, str] = {}
        self._arrived_hold_counts: dict[str, int] = {}
        self._load_confirmed_by_robot: dict[str, bool] = {}
        self._unload_confirmed_by_robot: dict[str, bool] = {}
        self._dispatch_tasks: dict[str, asyncio.Task[bool]] = {}

        self._loop_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._dispatch_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._subscription_ids: list[str] = []
        self._subscriptions_active = False

    async def start(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return

        self._running = True
        self._stop_event = asyncio.Event()
        self._subscribe_events()
        self._loop_task = asyncio.create_task(self._run_loop(), name="sumitomo-dispatcher")
        self._publish_event(EventName.SYSTEM_STARTED, {"module": "dispatcher"})
        logger.info("Dispatcher started")

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
            await self._cancel_dispatch_tasks(reason="dispatcher stopping")
        finally:
            self._loop_task = None
            self._unsubscribe_events()
            self._publish_event(EventName.SYSTEM_STOPPING, {"module": "dispatcher"})
            logger.info("Dispatcher stopped")

    async def pause(self, reason: str = "paused") -> None:
        if self._paused:
            return
        self._paused = True
        self._last_result = reason
        logger.info("Dispatcher paused", extra={"reason": reason})

    async def resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        logger.info("Dispatcher resumed")

    async def dispatch_once(self) -> bool:
        return await self._dispatch_for_robot()

    async def get_state(self) -> DispatchState:
        async with self._state_lock:
            return DispatchState(
                running=self._running,
                paused=self._paused,
                active_task_id=self._active_task_id,
                active_route_origin=self._active_route_origin,
                active_route_destination=self._active_route_destination,
                last_result=self._last_result,
                loop_iteration=self._loop_iteration,
            )

    def is_running(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    def is_paused(self) -> bool:
        return self._paused

    def active_task_id(self) -> str | None:
        return self._active_task_id

    def last_error(self) -> str | None:
        return self._last_error

    async def _run_loop(self) -> None:
        while self._running and not self._stop_event.is_set():
            self._loop_iteration += 1
            try:
                if not self._paused:
                    if self._registered_logistics_robot_ids():
                        self._schedule_registered_robot_dispatches()
                    else:
                        await self.dispatch_once()
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception("Dispatcher loop failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_seconds)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def _set_active_task_state(self, robot_id: str, task_id: str, origin_id: str, destination_id: str) -> None:
        async with self._state_lock:
            self._active_tasks[robot_id] = task_id
            self._active_route_origins[robot_id] = origin_id
            self._active_route_destinations[robot_id] = destination_id
            self._arrived_hold_counts[robot_id] = 0
            self._load_confirmed_by_robot[robot_id] = False
            self._unload_confirmed_by_robot[robot_id] = False
            if robot_id == self._legacy_state_robot_id():
                self._active_task_id = task_id
                self._active_route_origin = origin_id
                self._active_route_destination = destination_id
                self._arrived_hold_count = 0
                self._load_confirmed = False
                self._unload_confirmed = False
            self._last_result = None

    async def _reset_active_task_state(self, robot_id: str) -> None:
        async with self._state_lock:
            self._active_tasks.pop(robot_id, None)
            self._active_route_origins.pop(robot_id, None)
            self._active_route_destinations.pop(robot_id, None)
            self._arrived_hold_counts.pop(robot_id, None)
            self._load_confirmed_by_robot.pop(robot_id, None)
            self._unload_confirmed_by_robot.pop(robot_id, None)
            if robot_id == self._legacy_state_robot_id():
                self._active_task_id = None
                self._active_route_origin = None
                self._active_route_destination = None
                self._arrived_hold_count = 0
                self._load_confirmed = False
                self._unload_confirmed = False

    def _subscribe_events(self) -> None:
        if self._subscriptions_active:
            return
        event_bus = get_event_bus()
        self._subscription_ids = [
            event_bus.subscribe(EventName.QUADRUPED_IDLE, self._on_quadruped_idle),
            event_bus.subscribe(EventName.QUADRUPED_ARRIVED_AT_WAYPOINT, self._on_arrived_at_waypoint),
            event_bus.subscribe(EventName.HUMAN_CONFIRMED_LOAD, self._on_human_confirmed_load),
            event_bus.subscribe(EventName.HUMAN_CONFIRMED_UNLOAD, self._on_human_confirmed_unload),
            event_bus.subscribe(EventName.NAVIGATION_BLOCKED, self._on_navigation_blocked),
            event_bus.subscribe(EventName.NAVIGATION_COMPLETED, self._on_navigation_completed),
            event_bus.subscribe(EventName.NAVIGATION_FAILED, self._on_navigation_failed),
        ]
        self._subscriptions_active = True

    def _unsubscribe_events(self) -> None:
        if not self._subscriptions_active:
            return
        event_bus = get_event_bus()
        for subscription_id in self._subscription_ids:
            event_bus.unsubscribe(subscription_id)
        self._subscription_ids = []
        self._subscriptions_active = False

    async def _on_quadruped_idle(self, event) -> None:
        robot_id = self._resolve_event_robot_id(event)
        if robot_id is None and self._registered_logistics_robot_ids():
            return
        logger.info("Dispatcher received robot idle", extra={"robot_id": robot_id, "event_type": "robot_idle"})
        self._schedule_dispatch_for_robot(robot_id)

    async def _on_arrived_at_waypoint(self, event) -> None:
        robot_id = self._resolve_event_robot_id(event)
        if robot_id is None or not self._task_matches_event(event, robot_id):
            return
        if not bool(event.payload.get("hold")):
            return

        self._arrived_hold_counts[robot_id] = self._arrived_hold_counts.get(robot_id, 0) + 1
        if robot_id == self._legacy_state_robot_id():
            self._arrived_hold_count = self._arrived_hold_counts[robot_id]
        task = await self._task_queue.get_task(self._active_tasks[robot_id])
        if self._arrived_hold_counts[robot_id] == 1 and task.status == "dispatched":
            await self._apply_event_driven_transition(robot_id, "awaiting_load")
        elif self._arrived_hold_counts[robot_id] >= 2 and task.status in {"in_transit", "awaiting_load"}:
            await self._apply_event_driven_transition(robot_id, "awaiting_unload")

    async def _on_human_confirmed_load(self, event) -> None:
        robot_id = self._resolve_event_robot_id(event)
        if robot_id is None or not self._task_matches_event(event, robot_id):
            return
        self._load_confirmed_by_robot[robot_id] = True
        if robot_id == self._legacy_state_robot_id():
            self._load_confirmed = True
        task = await self._task_queue.get_task(self._active_tasks[robot_id])
        if task.status == "awaiting_load":
            await self._apply_event_driven_transition(robot_id, "in_transit")

    async def _on_human_confirmed_unload(self, event) -> None:
        robot_id = self._resolve_event_robot_id(event)
        if robot_id is None or not self._task_matches_event(event, robot_id):
            return
        self._unload_confirmed_by_robot[robot_id] = True
        if robot_id == self._legacy_state_robot_id():
            self._unload_confirmed = True
        task = await self._task_queue.get_task(self._active_tasks[robot_id])
        if task.status == "awaiting_unload":
            await self._apply_event_driven_transition(robot_id, "completed")

    async def _on_navigation_blocked(self, event) -> None:
        robot_id = self._resolve_event_robot_id(event)
        if robot_id is None or not self._task_matches_event(event, robot_id):
            return
        self._last_result = "blocked"
        logger.warning("Dispatcher received navigation blocked event", extra={"task_id": self._active_tasks.get(robot_id)})

    async def _on_navigation_completed(self, event) -> None:
        robot_id = self._resolve_event_robot_id(event)
        if robot_id is None or not self._task_matches_event(event, robot_id):
            return
        self._last_result = "navigation_completed"

    async def _on_navigation_failed(self, event) -> None:
        robot_id = self._resolve_event_robot_id(event)
        if robot_id is None or not self._task_matches_event(event, robot_id):
            return
        self._last_result = "navigation_failed"

    async def _apply_event_driven_transition(self, robot_id: str, target_status: str) -> None:
        task_id = self._active_tasks.get(robot_id)
        if task_id is None:
            return
        try:
            if target_status == "awaiting_load":
                await self._task_queue.mark_awaiting_load(task_id)
            elif target_status == "in_transit":
                await self._task_queue.mark_in_transit(task_id)
            elif target_status == "awaiting_unload":
                await self._task_queue.mark_awaiting_unload(task_id)
            elif target_status == "completed":
                await self._task_queue.mark_completed(task_id)
            else:
                raise DispatcherError(f"Unsupported transition target: {target_status}")
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning(
                "Dispatcher event-driven transition failed",
                extra={"task_id": task_id, "target_status": target_status},
            )

    async def _interpret_navigation_result(self, robot_id: str, task_id: str, result: NavigationResult) -> None:
        if result.blocked:
            await self._mark_failed_if_possible(task_id, "navigation blocked")
            self._last_result = "blocked"
            logger.warning("Dispatcher marked task failed after blocked navigation", extra={"task_id": task_id, "robot_id": robot_id, "status": "failed"})
            return
        if result.cancelled:
            await self._mark_failed_if_possible(task_id, "navigation cancelled")
            self._last_result = "cancelled"
            logger.warning("Dispatcher marked task failed after cancelled navigation", extra={"task_id": task_id, "robot_id": robot_id, "status": "cancelled"})
            return
        if not result.success:
            await self._mark_failed_if_possible(task_id, result.message or "navigation failed")
            self._last_result = "failed"
            logger.warning("Dispatcher marked task failed after navigation failure", extra={"task_id": task_id, "robot_id": robot_id, "status": "failed"})
            return

        current_task = await self._task_queue.get_task(task_id)
        if current_task.status == "completed":
            self._last_result = "completed"
            logger.info("Dispatcher completed task", extra={"task_id": task_id, "robot_id": robot_id, "status": "completed"})
            return
        if self._unload_confirmed_by_robot.get(robot_id, False) and current_task.status == "awaiting_unload":
            await self._task_queue.mark_completed(task_id)
            self._last_result = "completed"
            logger.info("Dispatcher completed task after unload confirmation", extra={"task_id": task_id, "robot_id": robot_id, "status": "completed"})
            return
        if self._arrived_hold_counts.get(robot_id, 0) == 0:
            await self._task_queue.mark_completed(task_id)
            self._last_result = "completed"
            logger.info("Dispatcher completed task after route execution", extra={"task_id": task_id, "robot_id": robot_id, "status": "completed"})
            return

        self._last_result = current_task.status

    async def _mark_failed_if_possible(self, task_id: str, notes: str) -> None:
        task = await self._task_queue.get_task(task_id)
        if task.status not in TERMINAL_TASK_STATUSES:
            await self._task_queue.mark_failed(task_id, notes=notes)

    async def _fail_active_task_if_possible(self, robot_id: str, notes: str) -> None:
        task_id = self._active_tasks.get(robot_id)
        if task_id is None:
            return
        try:
            await self._mark_failed_if_possible(task_id, notes)
        except Exception:
            logger.warning("Dispatcher could not fail active task after internal error")

    def _task_matches_event(self, event, robot_id: str) -> bool:
        task_id = self._active_tasks.get(robot_id)
        if task_id is None:
            return False
        event_task_id = getattr(event, "task_id", None) or event.payload.get("task_id")
        if event_task_id is None:
            return True
        return event_task_id == task_id

    async def _dispatch_for_robot(self, robot_id: str | None = None) -> bool:
        if self._paused:
            return False

        temporary_subscription = False
        if not self._subscriptions_active:
            self._subscribe_events()
            temporary_subscription = True

        processed = False
        resolved_robot_id: str | None = None
        try:
            dispatch_target = self._resolve_dispatch_target(robot_id)
            if dispatch_target is None:
                return False

            resolved_robot_id, navigator, state_monitor = dispatch_target
            if resolved_robot_id in self._active_tasks or navigator.is_navigating():
                return False

            state = await state_monitor.get_current_state()
            if state is None:
                state = await state_monitor.poll_once()
            if state is None:
                logger.warning("Dispatcher has no quadruped state available", extra={"robot_id": resolved_robot_id})
                return False
            if not state.connection_ok:
                logger.warning("Dispatcher skipped because quadruped is disconnected", extra={"robot_id": resolved_robot_id})
                return False

            robot_position = (state.position[0], state.position[1])
            async with self._dispatch_lock:
                if resolved_robot_id in self._active_tasks or navigator.is_navigating():
                    return False

                task = await self._task_queue.get_next_task(robot_position=robot_position)
                if task is None:
                    return False

                processed = True
                await self._set_active_task_state(resolved_robot_id, task.id, task.station_id, task.destination_id)
                logger.info(
                    "Dispatcher selected task",
                    extra={
                        "robot_id": resolved_robot_id,
                        "task_id": task.id,
                        "origin": task.station_id,
                        "destination": task.destination_id,
                        "status": "selected",
                    },
                )
                await self._task_queue.mark_dispatched(task.id)
                logger.info(
                    "Dispatcher dispatched task",
                    extra={
                        "robot_id": resolved_robot_id,
                        "task_id": task.id,
                        "origin": task.station_id,
                        "destination": task.destination_id,
                        "status": "dispatched",
                    },
                )

            result = await navigator.execute_route(task.station_id, task.destination_id, task_id=task.id)
            await self._interpret_navigation_result(resolved_robot_id, task.id, result)
            return True
        except Exception as exc:
            self._last_error = str(exc)
            logger.exception("Dispatcher dispatch failed", extra={"robot_id": resolved_robot_id})
            if processed and resolved_robot_id is not None:
                await self._fail_active_task_if_possible(resolved_robot_id, str(exc))
            return processed
        finally:
            if processed and resolved_robot_id is not None:
                await self._reset_active_task_state(resolved_robot_id)
            if temporary_subscription and not self._running:
                self._unsubscribe_events()

    def _schedule_registered_robot_dispatches(self) -> None:
        for robot_id in self._registered_logistics_robot_ids():
            self._schedule_dispatch_for_robot(robot_id)

    def _schedule_dispatch_for_robot(self, robot_id: str | None) -> None:
        dispatch_target = self._resolve_dispatch_target(robot_id)
        if dispatch_target is None:
            return

        resolved_robot_id, navigator, _state_monitor = dispatch_target
        existing_task = self._dispatch_tasks.get(resolved_robot_id)
        if existing_task is not None and not existing_task.done():
            return
        if resolved_robot_id in self._active_tasks or navigator.is_navigating():
            return

        task = asyncio.create_task(
            self._dispatch_for_robot(resolved_robot_id),
            name=f"sumitomo-dispatcher-{resolved_robot_id}",
        )
        self._dispatch_tasks[resolved_robot_id] = task
        task.add_done_callback(lambda completed_task, rid=resolved_robot_id: self._on_dispatch_task_done(rid, completed_task))

    def _on_dispatch_task_done(self, robot_id: str, task: asyncio.Task[bool]) -> None:
        try:
            task.result()
        except Exception:
            logger.exception("Dispatcher background dispatch failed", extra={"robot_id": robot_id})

    async def _cancel_dispatch_tasks(self, reason: str) -> None:
        active_tasks = [(robot_id, task) for robot_id, task in self._dispatch_tasks.items() if not task.done()]
        if not active_tasks:
            self._dispatch_tasks.clear()
            return

        for robot_id, _task in active_tasks:
            navigator = self._resolve_navigator_for_robot(robot_id)
            if navigator is None or not hasattr(navigator, "cancel_navigation"):
                continue
            try:
                await navigator.cancel_navigation(reason)
            except Exception:
                logger.warning("Dispatcher could not cancel navigation during stop", extra={"robot_id": robot_id})

        await asyncio.gather(*(task for _robot_id, task in active_tasks), return_exceptions=True)
        self._dispatch_tasks.clear()

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

    def _resolve_dispatch_target(
        self,
        robot_id: str | None,
    ) -> tuple[str, Navigator, StateMonitor] | None:
        if robot_id is not None:
            if robot_id == _LEGACY_ROBOT_ID and not self._registered_logistics_robot_ids():
                return (_LEGACY_ROBOT_ID, self._navigator, self._state_monitor)
            return self._resolve_registered_dispatch_target(robot_id)

        registered_robot_ids = self._registered_logistics_robot_ids()
        if registered_robot_ids:
            return self._resolve_registered_dispatch_target(registered_robot_ids[0])
        return (_LEGACY_ROBOT_ID, self._navigator, self._state_monitor)

    def _resolve_registered_dispatch_target(
        self,
        robot_id: str,
    ) -> tuple[str, Navigator, StateMonitor] | None:
        try:
            platform = self._robot_registry.get(robot_id)
        except RobotNotFoundError:
            logger.warning("Dispatcher ignored event for unknown robot", extra={"robot_id": robot_id})
            return None

        role = getattr(platform.config, "role", None)
        if role is None:
            role = getattr(platform.config.connection, "role", None)
        if role is not None and role != _LOGISTICS_ROLE:
            logger.warning("Dispatcher ignored non-logistics robot", extra={"robot_id": robot_id, "role": role})
            return None
        return (robot_id, platform.navigator, platform.state_monitor)

    def _resolve_navigator_for_robot(self, robot_id: str) -> Navigator | None:
        if robot_id == _LEGACY_ROBOT_ID:
            return self._navigator

        try:
            return self._robot_registry.get(robot_id).navigator
        except RobotNotFoundError:
            return None

    def _resolve_event_robot_id(self, event) -> str | None:
        payload = getattr(event, "payload", {})
        if not isinstance(payload, dict):
            payload = {}

        event_robot_id = payload.get("robot_id")
        if isinstance(event_robot_id, str) and event_robot_id:
            if self._resolve_registered_dispatch_target(event_robot_id) is not None:
                return event_robot_id
            return None

        registered_robot_ids = self._registered_logistics_robot_ids()
        if registered_robot_ids:
            return registered_robot_ids[0]
        return _LEGACY_ROBOT_ID

    def _legacy_state_robot_id(self) -> str:
        registered_robot_ids = self._registered_logistics_robot_ids()
        if registered_robot_ids:
            return registered_robot_ids[0]
        return _LEGACY_ROBOT_ID

    def _publish_event(self, event_name: EventName, payload: dict[str, Any]) -> None:
        try:
            get_event_bus().publish_nowait(event_name, payload=payload, source=__name__)
        except Exception:
            logger.debug("Dispatcher event publish skipped", extra={"event_name": event_name.value})


dispatcher = Dispatcher()


def get_dispatcher() -> Dispatcher:
    return dispatcher


__all__ = [
    "DispatchState",
    "Dispatcher",
    "DispatcherError",
    "dispatcher",
    "get_dispatcher",
]
