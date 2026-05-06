from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from apps.patrol.observation.observer import Observer, get_observer
from shared.core.config import get_config
from apps.patrol import events as patrol_events
from apps.patrol.config import get_patrol_config
from shared.core.event_bus import EventBus, EventName, get_event_bus
from shared.core.logger import get_logger
from shared.navigation.navigator import Navigator, get_navigator
from shared.quadruped.robot_registry import RobotNotFoundError, RobotRegistry, get_robot_registry

if TYPE_CHECKING:
    from apps.patrol.tasks.patrol_queue import PatrolQueue


logger = get_logger(__name__)

_UNSET = object()
_LEGACY_ROBOT_ID = "default"
_PATROL_ROLE = "patrol"


class PatrolDispatcherError(Exception):
    """Raised when patrol dispatcher configuration or lifecycle operations fail."""


@dataclass(frozen=True)
class PatrolDispatcherState:
    running: bool
    suspended: bool
    active_cycle_id: str | None
    active_route_id: str | None
    consecutive_failures: int
    last_result: str | None
    loop_iteration: int


def _load_default_patrol_queue() -> "PatrolQueue":
    queue_module = importlib.import_module("apps.patrol.tasks.patrol_queue")
    getter = getattr(queue_module, "get_patrol_queue", None)
    if callable(getter):
        return getter()
    return queue_module.PatrolQueue()


class PatrolDispatcher:
    def __init__(
        self,
        patrol_queue: PatrolQueue | None = None,
        navigator: Navigator | None = None,
        observer: Observer | None = None,
        event_bus: EventBus | None = None,
        poll_interval_seconds: float = 1.0,
        dock_route_id: str = "PATROL_TO_DOCK",
        robot_registry: RobotRegistry | None = None,
    ) -> None:
        if not isinstance(poll_interval_seconds, (int, float)) or float(poll_interval_seconds) <= 0:
            raise PatrolDispatcherError("poll_interval_seconds must be > 0")
        if not isinstance(dock_route_id, str) or not dock_route_id.strip():
            raise PatrolDispatcherError("dock_route_id must not be empty")

        self._patrol_queue = patrol_queue or _load_default_patrol_queue()
        self._navigator = navigator or get_navigator()
        self._observer = observer or get_observer()
        self._event_bus = event_bus or get_event_bus()
        self._robot_registry = robot_registry or get_robot_registry()
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._dock_route_id = dock_route_id.strip()

        self._running = False
        self._suspended = False
        self._active_cycle_id: str | None = None
        self._active_route_id: str | None = None
        self._consecutive_failures = 0
        self._last_result: str | None = None
        self._loop_iteration = 0
        self._last_error: str | None = None
        self._active_cycles: dict[str, str] = {}
        self._active_routes: dict[str, str] = {}
        self._waypoint_events_by_robot: dict[str, list[dict[str, Any]]] = {}
        self._dispatch_tasks: dict[str, asyncio.Task[bool]] = {}

        self._state_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._dispatch_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._subscription_ids: list[str] = []
        self._waypoint_events: list[dict[str, Any]] = []

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._running:
                return

            await self._event_bus.start()
            self._subscribe_events()
            self._stop_event.clear()
            await self._set_state(running=True)
            self._task = asyncio.create_task(self._run_loop(), name="patrol-dispatcher")
            logger.info("Patrol dispatcher started", extra={"poll_interval_seconds": self._poll_interval_seconds})

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if not self._running and self._task is None:
                self._unsubscribe_all()
                return

            await self._set_state(running=False)
            self._stop_event.set()
            await self._cancel_active_dispatches("dispatcher stopped")

            task = self._task
            self._task = None
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            self._unsubscribe_all()
            logger.info("Patrol dispatcher stopped")

    async def dispatch_once(self) -> bool:
        return await self._dispatch_for_robot()

    async def _dispatch_for_robot(self, robot_id: str | None = None) -> bool:
        if self._suspended:
            return False

        dispatch_target = self._resolve_dispatch_target(robot_id)
        if dispatch_target is None:
            return False

        resolved_robot_id, navigator = dispatch_target
        if resolved_robot_id in self._active_cycles:
            return False
        if navigator.is_navigating():
            return False

        cycle = None
        try:
            async with self._dispatch_lock:
                if resolved_robot_id in self._active_cycles or navigator.is_navigating():
                    return False
                cycle = await self._get_next_cycle_for_robot(resolved_robot_id)
                if cycle is None:
                    return False

                await self._set_active_cycle_state(resolved_robot_id, cycle.cycle_id, cycle.route_id)
                logger.info(
                    "Patrol cycle selected",
                    extra={
                        "robot_id": resolved_robot_id,
                        "cycle_id": cycle.cycle_id,
                        "route_id": cycle.route_id,
                        "status": "selected",
                    },
                )
                await self._patrol_queue.mark_active(cycle.cycle_id)
                logger.info(
                    "Patrol cycle dispatched",
                    extra={
                        "robot_id": resolved_robot_id,
                        "cycle_id": cycle.cycle_id,
                        "route_id": cycle.route_id,
                        "status": "dispatched",
                    },
                )

            result = await navigator.execute_route_by_id(cycle.route_id, task_id=cycle.cycle_id)
            observed_count, anomaly_ids = await self._process_waypoint_events(resolved_robot_id, cycle.cycle_id)

            if result.success:
                stats = {
                    "waypoints_total": result.total_waypoints,
                    "waypoints_observed": observed_count,
                    "anomaly_ids": anomaly_ids,
                }
                await self._patrol_queue.mark_completed(cycle.cycle_id, stats_dict=stats)
                await self._set_state(consecutive_failures=0, last_result=result.message or "completed")
                self._last_error = None
                await self._publish_event(
                    patrol_events.PATROL_CYCLE_COMPLETED,
                    {
                        "cycle_id": cycle.cycle_id,
                        "route_id": cycle.route_id,
                        "status": "completed",
                        "robot_id": resolved_robot_id,
                        **stats,
                    },
                    task_id=cycle.cycle_id,
                )
                logger.info(
                    "Patrol cycle completed",
                    extra={
                        "robot_id": resolved_robot_id,
                        "cycle_id": cycle.cycle_id,
                        "route_id": cycle.route_id,
                        "status": "completed",
                    },
                )
                await self._return_to_dock(resolved_robot_id)
                return True

            reason = self._navigation_failure_reason(result)
            await self._handle_cycle_failure(resolved_robot_id, cycle.cycle_id, cycle.route_id, reason)
            return True
        except Exception as exc:
            if cycle is None:
                self._last_error = str(exc)
                logger.exception("Patrol dispatcher cycle selection failed", extra={"robot_id": resolved_robot_id})
                return False
            self._last_error = str(exc)
            logger.exception(
                "Patrol dispatcher cycle failed unexpectedly",
                extra={"cycle_id": cycle.cycle_id, "route_id": cycle.route_id, "robot_id": resolved_robot_id},
            )
            await self._handle_cycle_failure(resolved_robot_id, cycle.cycle_id, cycle.route_id, str(exc), mark_failed=True)
            return True
        finally:
            if cycle is not None:
                await self._clear_active_cycle_state(resolved_robot_id)

    async def suspend(self, reason: str = "suspended") -> None:
        normalized_reason = self._normalize_reason(reason, default="suspended")
        if self._suspended:
            return
        await self._set_state(suspended=True, last_result=normalized_reason)
        await self._cancel_active_dispatches(normalized_reason)
        await self._publish_event(patrol_events.PATROL_SUSPENDED, {"reason": normalized_reason}, task_id=self._active_cycle_id)

    async def resume(self, reason: str = "resumed") -> None:
        normalized_reason = self._normalize_reason(reason, default="resumed")
        if not self._suspended:
            return
        await self._set_state(suspended=False, last_result=normalized_reason)
        await self._publish_event(patrol_events.PATROL_RESUMED, {"reason": normalized_reason})

    async def get_state(self) -> PatrolDispatcherState:
        async with self._state_lock:
            return PatrolDispatcherState(
                running=self._running,
                suspended=self._suspended,
                active_cycle_id=self._active_cycle_id,
                active_route_id=self._active_route_id,
                consecutive_failures=self._consecutive_failures,
                last_result=self._last_result,
                loop_iteration=self._loop_iteration,
            )

    def is_running(self) -> bool:
        return self._running

    def is_suspended(self) -> bool:
        return self._suspended

    def active_cycle_id(self) -> str | None:
        return self._active_cycle_id

    def last_error(self) -> str | None:
        return self._last_error

    async def _run_loop(self) -> None:
        while self._running:
            await self._increment_loop_iteration()
            try:
                if not self._suspended:
                    patrol_robot_ids = self._registered_patrol_robot_ids()
                    if patrol_robot_ids:
                        self._schedule_patrol_dispatches(patrol_robot_ids)
                    else:
                        await self.dispatch_once()
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception("Patrol dispatcher loop iteration failed")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_seconds)
            except asyncio.TimeoutError:
                continue
            return

    def _subscribe_events(self) -> None:
        if self._subscription_ids:
            return
        self._subscription_ids = [
            self._event_bus.subscribe(
                patrol_events.PATROL_SUSPENDED,
                self._handle_patrol_suspended,
                subscriber_name="patrol-dispatcher",
            ),
            self._event_bus.subscribe(
                patrol_events.PATROL_RESUMED,
                self._handle_patrol_resumed,
                subscriber_name="patrol-dispatcher",
            ),
            self._event_bus.subscribe(
                EventName.ESTOP_TRIGGERED,
                self._handle_estop_triggered,
                subscriber_name="patrol-dispatcher",
            ),
            self._event_bus.subscribe(
                EventName.QUADRUPED_ARRIVED_AT_WAYPOINT,
                self._on_waypoint_arrived,
                subscriber_name="patrol-dispatcher",
            ),
            self._event_bus.subscribe(
                EventName.QUADRUPED_IDLE,
                self._on_quadruped_idle,
                subscriber_name="patrol-dispatcher",
            ),
        ]

    def _unsubscribe_all(self) -> None:
        for subscription_id in self._subscription_ids:
            self._event_bus.unsubscribe(subscription_id)
        self._subscription_ids = []

    async def _handle_patrol_suspended(self, _event: Any) -> None:
        await self._set_state(suspended=True)

    async def _handle_patrol_resumed(self, _event: Any) -> None:
        await self._set_state(suspended=False)

    async def _handle_estop_triggered(self, event: Any) -> None:
        reason = self._normalize_reason(getattr(event, "payload", {}).get("reason"), default="estop triggered")
        robot_id = self._resolve_event_robot_id(event)
        if robot_id is None and self._registered_patrol_robot_ids():
            return
        if robot_id == _LEGACY_ROBOT_ID and not self._registered_patrol_robot_ids():
            await self._set_state(suspended=True, last_result=reason)
            if self._active_cycle_id is not None:
                await self._cancel_navigation(reason)
            return
        if robot_id is not None and self._active_cycles.get(robot_id) is not None:
            await self._cancel_navigation(reason, robot_id=robot_id)

    async def _on_quadruped_idle(self, event: Any) -> None:
        robot_id = self._resolve_event_robot_id(event)
        if robot_id is None and self._registered_patrol_robot_ids():
            return
        logger.info("Patrol dispatcher received robot idle", extra={"robot_id": robot_id, "event_type": "robot_idle"})
        self._schedule_dispatch_for_robot(robot_id)

    async def _on_waypoint_arrived(self, event: Any) -> None:
        robot_id = self._resolve_event_robot_id(event)
        if robot_id is None:
            return

        active_cycle_id = self._active_cycles.get(robot_id)
        if active_cycle_id is None:
            return
        payload = dict(getattr(event, "payload", {}) or {})
        payload_task_id = payload.get("task_id") or payload.get("cycle_id") or getattr(event, "task_id", None)
        if payload_task_id is not None and payload_task_id != active_cycle_id:
            return

        self._waypoint_events_by_robot.setdefault(robot_id, []).append(payload)

    async def _process_waypoint_events(self, robot_id: str, cycle_id: str) -> tuple[int, list[str]]:
        observed_count = 0
        anomaly_ids: list[str] = []

        for payload in list(self._waypoint_events_by_robot.get(robot_id, [])):
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                continue

            if metadata.get("observe") is not True:
                continue

            zone_id = metadata.get("zone_id")
            if not isinstance(zone_id, str) or not zone_id.strip():
                continue

            waypoint_name = payload.get("waypoint_name")
            normalized_waypoint_name = waypoint_name if isinstance(waypoint_name, str) and waypoint_name else "unknown"

            try:
                summary = await self._observer.observe(normalized_waypoint_name, zone_id.strip(), cycle_id)
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception(
                    "Patrol waypoint observation failed",
                    extra={"cycle_id": cycle_id, "waypoint_name": normalized_waypoint_name, "zone_id": zone_id},
                )
                continue

            observed_count += 1
            anomaly_id = getattr(summary, "anomaly_id", None)
            if isinstance(anomaly_id, str) and anomaly_id:
                anomaly_ids.append(anomaly_id)

        return observed_count, anomaly_ids

    async def _handle_cycle_failure(
        self,
        robot_id: str,
        cycle_id: str,
        route_id: str,
        reason: str,
        *,
        mark_failed: bool = True,
    ) -> None:
        normalized_reason = self._normalize_reason(reason, default="navigation failed")
        failures = self._consecutive_failures + 1
        await self._set_state(consecutive_failures=failures, last_result=normalized_reason)

        if mark_failed:
            try:
                await self._patrol_queue.mark_failed(cycle_id, normalized_reason)
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning("Patrol cycle mark_failed skipped", extra={"cycle_id": cycle_id, "reason": normalized_reason})

        await self._publish_event(
            patrol_events.PATROL_CYCLE_FAILED,
            {
                "cycle_id": cycle_id,
                "route_id": route_id,
                "robot_id": robot_id,
                "status": "failed",
                "reason": normalized_reason,
            },
            task_id=cycle_id,
        )

        max_failures = int(get_patrol_config(get_config()).max_consecutive_failures)
        if failures >= max_failures:
            await self._set_state(suspended=True)
            await self._publish_event(
                patrol_events.PATROL_SUSPENDED,
                {"reason": "max_consecutive_failures", "robot_id": robot_id},
                task_id=cycle_id,
            )

    async def _return_to_dock(self, robot_id: str) -> None:
        navigator = self._resolve_navigator_for_robot(robot_id)
        if navigator is None:
            return
        try:
            result = await navigator.execute_route_by_id(self._dock_route_id)
        except Exception as exc:
            logger.warning("Patrol dock route failed to start", extra={"route_id": self._dock_route_id, "error": str(exc)})
            return

        if not getattr(result, "success", False):
            logger.warning(
                "Patrol dock route failed",
                extra={"route_id": self._dock_route_id, "message": getattr(result, "message", "dock failed")},
            )

    async def _cancel_navigation(self, reason: str, *, robot_id: str | None = None) -> None:
        navigator = self._navigator if robot_id is None else self._resolve_navigator_for_robot(robot_id)
        if navigator is None:
            return
        cancel_navigation = getattr(navigator, "cancel_navigation", None)
        if cancel_navigation is None:
            return
        try:
            await cancel_navigation(reason=reason)
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("Patrol dispatcher failed to cancel navigation", extra={"reason": reason})

    async def _publish_event(
        self,
        event_name: EventName | str,
        payload: dict[str, Any],
        *,
        task_id: str | None = None,
    ) -> None:
        try:
            await self._event_bus.publish(event_name, payload=payload, source=__name__, task_id=task_id)
        except Exception:
            logger.warning("Patrol dispatcher event publish skipped", extra={"event_name": event_name.value})

    async def _set_state(
        self,
        *,
        running: bool | object = _UNSET,
        suspended: bool | object = _UNSET,
        active_cycle_id: str | None | object = _UNSET,
        active_route_id: str | None | object = _UNSET,
        consecutive_failures: int | object = _UNSET,
        last_result: str | None | object = _UNSET,
        loop_iteration: int | object = _UNSET,
    ) -> None:
        async with self._state_lock:
            if running is not _UNSET:
                self._running = bool(running)
            if suspended is not _UNSET:
                self._suspended = bool(suspended)
            if active_cycle_id is not _UNSET:
                self._active_cycle_id = active_cycle_id
            if active_route_id is not _UNSET:
                self._active_route_id = active_route_id
            if consecutive_failures is not _UNSET:
                self._consecutive_failures = int(consecutive_failures)
            if last_result is not _UNSET:
                self._last_result = last_result
            if loop_iteration is not _UNSET:
                self._loop_iteration = int(loop_iteration)

    async def _set_active_cycle_state(self, robot_id: str, cycle_id: str, route_id: str) -> None:
        self._active_cycles[robot_id] = cycle_id
        self._active_routes[robot_id] = route_id
        self._waypoint_events_by_robot[robot_id] = []
        if robot_id == self._legacy_state_robot_id():
            self._waypoint_events = []
            await self._set_state(active_cycle_id=cycle_id, active_route_id=route_id)

    async def _clear_active_cycle_state(self, robot_id: str) -> None:
        self._active_cycles.pop(robot_id, None)
        self._active_routes.pop(robot_id, None)
        self._waypoint_events_by_robot.pop(robot_id, None)
        if robot_id == self._legacy_state_robot_id():
            self._waypoint_events = []
            await self._clear_active_state()

    def _schedule_patrol_dispatches(self, patrol_robot_ids: list[str]) -> None:
        for robot_id in patrol_robot_ids:
            self._schedule_dispatch_for_robot(robot_id)

    def _schedule_dispatch_for_robot(self, robot_id: str | None) -> None:
        dispatch_target = self._resolve_dispatch_target(robot_id)
        if dispatch_target is None:
            return
        resolved_robot_id, navigator = dispatch_target
        existing_task = self._dispatch_tasks.get(resolved_robot_id)
        if existing_task is not None and not existing_task.done():
            return
        if resolved_robot_id in self._active_cycles or navigator.is_navigating():
            return
        task = asyncio.create_task(
            self._dispatch_for_robot(resolved_robot_id),
            name=f"patrol-dispatcher-{resolved_robot_id}",
        )
        self._dispatch_tasks[resolved_robot_id] = task
        task.add_done_callback(lambda completed_task, rid=resolved_robot_id: self._on_dispatch_task_done(rid, completed_task))

    def _on_dispatch_task_done(self, robot_id: str, task: asyncio.Task[bool]) -> None:
        current_task = self._dispatch_tasks.get(robot_id)
        if current_task is task:
            self._dispatch_tasks.pop(robot_id, None)
        try:
            task.result()
        except Exception:
            logger.exception("Patrol dispatcher background dispatch failed", extra={"robot_id": robot_id})

    async def _cancel_active_dispatches(self, reason: str) -> None:
        active_robot_ids = [robot_id for robot_id in self._active_cycles if self._active_cycles.get(robot_id) is not None]
        for robot_id in active_robot_ids:
            await self._cancel_navigation(reason, robot_id=robot_id)
        active_tasks = [task for task in self._dispatch_tasks.values() if not task.done()]
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        self._dispatch_tasks.clear()

    async def _get_next_cycle_for_robot(self, robot_id: str):
        try:
            cycle = await self._patrol_queue.get_next_cycle(robot_id=robot_id)
        except TypeError:
            cycle = await self._patrol_queue.get_next_cycle()
        if cycle is None:
            return None
        cycle_robot_id = getattr(cycle, "robot_id", None)
        if cycle_robot_id is not None and cycle_robot_id != robot_id:
            return None
        return cycle

    def _resolve_dispatch_target(self, robot_id: str | None) -> tuple[str, Navigator] | None:
        if robot_id is not None:
            if robot_id == _LEGACY_ROBOT_ID and not self._registered_patrol_robot_ids():
                return (_LEGACY_ROBOT_ID, self._navigator)
            return self._resolve_registered_dispatch_target(robot_id)
        patrol_robot_ids = self._registered_patrol_robot_ids()
        if patrol_robot_ids:
            return self._resolve_registered_dispatch_target(patrol_robot_ids[0])
        return (_LEGACY_ROBOT_ID, self._navigator)

    def _resolve_registered_dispatch_target(self, robot_id: str) -> tuple[str, Navigator] | None:
        try:
            platform = self._robot_registry.get(robot_id)
        except RobotNotFoundError:
            logger.warning("Patrol dispatcher ignored event for unknown robot", extra={"robot_id": robot_id})
            return None
        role = getattr(platform.config, "role", None)
        if role is None:
            role = getattr(platform.config.connection, "role", None)
        if role is not None and role != _PATROL_ROLE:
            logger.warning("Patrol dispatcher ignored non-patrol robot", extra={"robot_id": robot_id, "role": role})
            return None
        return (robot_id, platform.navigator)

    def _resolve_event_robot_id(self, event: Any) -> str | None:
        payload = getattr(event, "payload", {})
        if not isinstance(payload, dict):
            payload = {}
        event_robot_id = payload.get("robot_id")
        if isinstance(event_robot_id, str) and event_robot_id:
            if event_robot_id == _LEGACY_ROBOT_ID and not self._registered_patrol_robot_ids():
                return _LEGACY_ROBOT_ID
            if self._resolve_registered_dispatch_target(event_robot_id) is not None:
                return event_robot_id
            return None
        patrol_robot_ids = self._registered_patrol_robot_ids()
        if patrol_robot_ids:
            return patrol_robot_ids[0]
        return _LEGACY_ROBOT_ID

    def _registered_patrol_robot_ids(self) -> list[str]:
        robot_ids: list[str] = []
        for platform in self._robot_registry.all():
            role = getattr(platform.config, "role", None)
            if role is None:
                role = getattr(platform.config.connection, "role", None)
            if role is not None and role != _PATROL_ROLE:
                continue
            robot_ids.append(platform.robot_id)
        return robot_ids

    def _legacy_state_robot_id(self) -> str:
        patrol_robot_ids = self._registered_patrol_robot_ids()
        if patrol_robot_ids:
            return patrol_robot_ids[0]
        return _LEGACY_ROBOT_ID

    def _resolve_navigator_for_robot(self, robot_id: str) -> Navigator | None:
        if robot_id == _LEGACY_ROBOT_ID and not self._registered_patrol_robot_ids():
            return self._navigator
        try:
            return self._robot_registry.get(robot_id).navigator
        except RobotNotFoundError:
            return None

    async def _increment_loop_iteration(self) -> None:
        async with self._state_lock:
            self._loop_iteration += 1

    async def _clear_active_state(self) -> None:
        await self._set_state(active_cycle_id=None, active_route_id=None)

    @staticmethod
    def _normalize_reason(value: Any, *, default: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return default

    @staticmethod
    def _navigation_failure_reason(result: Any) -> str:
        message = getattr(result, "message", None)
        if isinstance(message, str) and message.strip():
            return message.strip()
        if getattr(result, "cancelled", False):
            return "navigation cancelled"
        if getattr(result, "blocked", False):
            return "navigation blocked"
        return "navigation failed"


patrol_dispatcher = PatrolDispatcher()


def get_patrol_dispatcher() -> PatrolDispatcher:
    return patrol_dispatcher


__all__ = [
    "PatrolDispatcher",
    "PatrolDispatcherError",
    "PatrolDispatcherState",
    "get_patrol_dispatcher",
    "patrol_dispatcher",
]
