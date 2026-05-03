from __future__ import annotations

"""Simple waypoint-following navigator for Phase 1 quadruped logistics."""

import asyncio
import math
import time
from dataclasses import dataclass, replace
from typing import Any

from shared.core.config import get_config
from shared.core.event_bus import EventName, get_event_bus
from shared.core.logger import get_logger
from shared.navigation.route_store import RouteDefinition, RouteStore, Waypoint, get_route_store
from shared.quadruped.heartbeat import HeartbeatController, get_heartbeat_controller
from shared.quadruped.sdk_adapter import SDKAdapter
from shared.quadruped.state_monitor import QuadrupedState, StateMonitor, get_state_monitor


logger = get_logger(__name__)

_CONTROL_LOOP_SECONDS = 0.05


class NavigatorError(Exception):
    """Raised when navigation cannot be started or completed safely."""


class NavigationBlockedError(NavigatorError):
    """Raised when navigation remains blocked beyond the configured timeout."""


class NavigationCancelledError(NavigatorError):
    """Raised when active navigation is cancelled."""


@dataclass(frozen=True)
class NavigationResult:
    success: bool
    route_id: str
    origin_id: str
    destination_id: str
    completed_waypoints: int
    total_waypoints: int
    blocked: bool = False
    cancelled: bool = False
    message: str = ""


def _normalize_angle_rad(angle: float) -> float:
    """Normalize an angle to [-pi, pi] while preserving exact boundary signs."""

    normalized = math.fmod(angle, 2.0 * math.pi)
    if normalized > math.pi:
        normalized -= 2.0 * math.pi
    elif normalized < -math.pi:
        normalized += 2.0 * math.pi
    return normalized


class Navigator:
    """Executes file-backed routes with a deterministic proportional controller."""

    def __init__(
        self,
        route_store: RouteStore | None = None,
        state_monitor: StateMonitor | None = None,
        heartbeat: HeartbeatController | None = None,
        waypoint_tolerance_m: float | None = None,
        heading_tolerance_deg: float | None = None,
        obstacle_hold_timeout_seconds: float | None = None,
        obstacle_stable_clear_seconds: float | None = None,
        obstacle_min_hold_seconds: float | None = None,
        obstacle_resume_ramp_seconds: float | None = None,
        obstacle_repeat_fallback_count: int | None = None,
        sdk_adapter: SDKAdapter | None = None,
        slam_provider: Any | None = None,
        robot_id: str = "default",
    ) -> None:
        config = get_config()
        self._route_store = route_store or get_route_store()
        self._state_monitor = state_monitor or get_state_monitor()
        self._heartbeat = heartbeat or get_heartbeat_controller()
        self._sdk_adapter = sdk_adapter
        self._waypoint_tolerance_m = (
            waypoint_tolerance_m if waypoint_tolerance_m is not None else config.navigation.waypoint_tolerance_m
        )
        self._heading_tolerance_deg = (
            heading_tolerance_deg if heading_tolerance_deg is not None else config.navigation.heading_tolerance_deg
        )
        self._obstacle_hold_timeout_seconds = (
            obstacle_hold_timeout_seconds
            if obstacle_hold_timeout_seconds is not None
            else config.navigation.obstacle_hold_timeout_seconds
        )
        self._obstacle_stable_clear_seconds = (
            obstacle_stable_clear_seconds
            if obstacle_stable_clear_seconds is not None
            else config.navigation.obstacle_stable_clear_seconds
        )
        self._obstacle_min_hold_seconds = (
            obstacle_min_hold_seconds
            if obstacle_min_hold_seconds is not None
            else config.navigation.obstacle_min_hold_seconds
        )
        self._obstacle_resume_ramp_seconds = (
            obstacle_resume_ramp_seconds
            if obstacle_resume_ramp_seconds is not None
            else config.navigation.obstacle_resume_ramp_seconds
        )
        self._obstacle_repeat_fallback_count = (
            obstacle_repeat_fallback_count
            if obstacle_repeat_fallback_count is not None
            else config.navigation.obstacle_repeat_fallback_count
        )
        self._position_source = config.navigation.position_source
        self._slam_provider = slam_provider
        if self._position_source == "slam" and self._slam_provider is None:
            from shared.navigation.slam import SLAMProvider

            self._slam_provider = SLAMProvider(state_monitor=self._state_monitor, enabled=True)
        if self._waypoint_tolerance_m <= 0:
            raise NavigatorError("waypoint_tolerance_m must be > 0")
        if self._heading_tolerance_deg <= 0:
            raise NavigatorError("heading_tolerance_deg must be > 0")
        if self._obstacle_hold_timeout_seconds <= 0:
            raise NavigatorError("obstacle_hold_timeout_seconds must be > 0")
        if not isinstance(robot_id, str) or not robot_id.strip():
            raise NavigatorError("robot_id must be a non-empty string")

        self.robot_id = robot_id
        self._navigation_lock = asyncio.Lock()
        self._is_navigating = False
        self._current_route_id: str | None = None
        self._completed_waypoints = 0
        self._last_error: str | None = None
        self._cancelled = False
        self._cancel_reason = "cancelled"
        self._blocked = False
        self._blocked_started_at: float | None = None
        self._hold_event = asyncio.Event()
        self._subscription_ids: list[str] = []
        self._active_task_id: str | None = None

        # Obstacle policy state — reset at the start of each navigation
        self._obstacle_count: int = 0
        self._obstacle_detected_at: float | None = None
        self._requires_hmi_confirmation: bool = False
        self._stable_clear_task: asyncio.Task[None] | None = None
        self._resume_ramp_started_at: float | None = None

    async def execute_route(
        self,
        origin_id: str,
        destination_id: str,
        *,
        task_id: str | None = None,
    ) -> NavigationResult:
        waypoints = await self._route_store.get_route(origin_id, destination_id)
        route = await self._resolve_route_definition(origin_id, destination_id, waypoints)
        return await self._execute_route_definition(route, task_id=task_id)

    async def execute_route_by_id(self, route_id: str, *, task_id: str | None = None) -> NavigationResult:
        route = await self._route_store.get_route_definition(route_id)
        return await self._execute_route_definition(route, task_id=task_id)

    async def cancel_navigation(self, reason: str = "cancelled") -> None:
        self._cancelled = True
        self._cancel_reason = reason
        self._hold_event.set()
        await self._clear_target_velocity("navigator_cancelled")
        logger.warning("Navigation cancelled", extra={"reason": reason, "route_id": self._current_route_id})

    def is_navigating(self) -> bool:
        return self._is_navigating

    def current_route_id(self) -> str | None:
        return self._current_route_id

    def completed_waypoint_count(self) -> int:
        return self._completed_waypoints

    def last_error(self) -> str | None:
        return self._last_error

    async def _execute_route_definition(
        self,
        route: RouteDefinition,
        *,
        task_id: str | None,
    ) -> NavigationResult:
        async with self._navigation_lock:
            if self._is_navigating:
                raise NavigatorError("navigation is already in progress")
            self._is_navigating = True
            self._current_route_id = route.id
            self._completed_waypoints = 0
            self._last_error = None
            self._cancelled = False
            self._cancel_reason = "cancelled"
            self._blocked = False
            self._blocked_started_at = None
            self._hold_event = asyncio.Event()
            self._active_task_id = task_id
            self._obstacle_count = 0
            self._obstacle_detected_at = None
            self._requires_hmi_confirmation = False
            self._stable_clear_task = None
            self._resume_ramp_started_at = None

        self._subscribe_navigation_events()
        self._publish_event(
            EventName.NAVIGATION_STARTED,
            self._base_payload(route, task_id),
            task_id=task_id,
        )
        logger.info("Navigation started", extra={"route_id": route.id, "task_id": task_id})

        try:
            for waypoint_index, waypoint in enumerate(route.waypoints):
                await self._drive_to_waypoint(route, waypoint, waypoint_index, task_id)
                await self._clear_target_velocity("navigator_waypoint_reached")
                self._completed_waypoints += 1
                self._publish_waypoint_arrival(route, waypoint, waypoint_index, task_id)
                logger.info(
                    "Waypoint reached",
                    extra={"route_id": route.id, "waypoint_name": waypoint.name, "task_id": task_id},
                )
                if waypoint.hold:
                    await self._wait_for_hold_confirmation(route, task_id)

            await self._clear_target_velocity("navigator_completed")
            result = NavigationResult(
                success=True,
                route_id=route.id,
                origin_id=route.origin_id,
                destination_id=route.destination_id,
                completed_waypoints=self._completed_waypoints,
                total_waypoints=len(route.waypoints),
                message="Navigation completed",
            )
            self._publish_event(EventName.NAVIGATION_COMPLETED, self._result_payload(result), task_id=task_id)
            logger.info("Navigation completed", extra={"route_id": route.id, "task_id": task_id})
            return result
        except NavigationCancelledError:
            result = NavigationResult(
                success=False,
                route_id=route.id,
                origin_id=route.origin_id,
                destination_id=route.destination_id,
                completed_waypoints=self._completed_waypoints,
                total_waypoints=len(route.waypoints),
                cancelled=True,
                message=self._cancel_reason,
            )
            return result
        except NavigationBlockedError as exc:
            result = NavigationResult(
                success=False,
                route_id=route.id,
                origin_id=route.origin_id,
                destination_id=route.destination_id,
                completed_waypoints=self._completed_waypoints,
                total_waypoints=len(route.waypoints),
                blocked=True,
                message=str(exc),
            )
            self._publish_event(EventName.NAVIGATION_BLOCKED, self._result_payload(result), task_id=task_id)
            return result
        except NavigatorError as exc:
            self._last_error = str(exc)
            result = NavigationResult(
                success=False,
                route_id=route.id,
                origin_id=route.origin_id,
                destination_id=route.destination_id,
                completed_waypoints=self._completed_waypoints,
                total_waypoints=len(route.waypoints),
                message=str(exc),
            )
            return result
        except Exception as exc:
            self._last_error = str(exc)
            logger.exception("Navigation failed unexpectedly", extra={"route_id": route.id, "task_id": task_id})
            result = NavigationResult(
                success=False,
                route_id=route.id,
                origin_id=route.origin_id,
                destination_id=route.destination_id,
                completed_waypoints=self._completed_waypoints,
                total_waypoints=len(route.waypoints),
                message=f"Navigation failed: {exc}",
            )
            self._publish_event(EventName.NAVIGATION_FAILED, self._result_payload(result), task_id=task_id)
            return result
        finally:
            self._cancel_stable_clear_task()
            await self._clear_target_velocity("navigator_exit")
            self._unsubscribe_navigation_events()
            self._is_navigating = False
            self._current_route_id = None
            self._blocked = False
            self._blocked_started_at = None
            self._active_task_id = None

    async def _drive_to_waypoint(
        self,
        route: RouteDefinition,
        waypoint: Waypoint,
        waypoint_index: int,
        task_id: str | None,
    ) -> None:
        while True:
            self._raise_if_cancelled()
            await self._wait_while_blocked(route, task_id)
            state = await self._get_state_or_poll()
            if state is None:
                raise NavigatorError("Quadruped state is unavailable")
            if not state.connection_ok:
                raise NavigatorError("Quadruped connection is not OK")

            dx = waypoint.x - state.position[0]
            dy = waypoint.y - state.position[1]
            distance = math.hypot(dx, dy)
            if distance <= self._waypoint_tolerance_m:
                return

            vx, vy, yaw_rate = self._compute_velocity_command(state, waypoint, dx, dy, distance)
            await self._heartbeat.set_target_velocity(
                vx,
                vy,
                yaw_rate,
                source="navigator",
                task_id=task_id,
            )
            logger.debug(
                "Navigator command updated",
                extra={
                    "route_id": route.id,
                    "waypoint_index": waypoint_index,
                    "distance": distance,
                    "task_id": task_id,
                },
            )
            await asyncio.sleep(_CONTROL_LOOP_SECONDS)

    async def _wait_for_hold_confirmation(self, route: RouteDefinition, task_id: str | None) -> None:
        self._hold_event.clear()
        while not self._hold_event.is_set():
            self._raise_if_cancelled()
            await self._wait_while_blocked(route, task_id)
            try:
                await asyncio.wait_for(self._hold_event.wait(), timeout=_CONTROL_LOOP_SECONDS)
                self._raise_if_cancelled()
            except asyncio.TimeoutError:
                continue

    async def _wait_while_blocked(self, route: RouteDefinition, task_id: str | None) -> None:
        while self._blocked:
            self._raise_if_cancelled()
            if self._blocked_started_at is not None:
                elapsed = asyncio.get_running_loop().time() - self._blocked_started_at
                if elapsed >= self._obstacle_hold_timeout_seconds:
                    self._cancel_stable_clear_task()
                    await self._clear_target_velocity("navigator_obstacle_timeout")
                    logger.warning("Navigation blocked by obstacle timeout", extra={"route_id": route.id})
                    raise NavigationBlockedError("Obstacle timeout")
            await asyncio.sleep(_CONTROL_LOOP_SECONDS)

    async def _get_state_or_poll(self) -> QuadrupedState | None:
        state = await self._state_monitor.get_current_state()
        if state is None:
            state = await self._state_monitor.poll_once()
        if state is not None and self._position_source == "slam" and self._slam_provider is not None:
            state = await self._apply_slam_position(state)
        return state

    async def _apply_slam_position(self, state: QuadrupedState) -> QuadrupedState:
        try:
            corrected_position = await self._slam_provider.get_corrected_position()
        except Exception as exc:
            logger.warning("Navigator falling back to odometry state", extra={"error": str(exc)})
            return state

        return replace(
            state,
            position=(corrected_position.x, corrected_position.y, state.position[2]),
            rpy=(state.rpy[0], state.rpy[1], corrected_position.heading_rad),
        )

    def _compute_velocity_command(
        self,
        state: QuadrupedState,
        waypoint: Waypoint,
        dx: float,
        dy: float,
        distance: float,
    ) -> tuple[float, float, float]:
        target_heading_rad = math.atan2(dy, dx)
        current_yaw = float(state.rpy[2])
        heading_error = _normalize_angle_rad(target_heading_rad - current_yaw)
        heading_tolerance_rad = math.radians(self._heading_tolerance_deg)

        ramp_factor = self._compute_resume_ramp_factor()
        forward_speed = min(waypoint.velocity, max(0.0, distance * 0.8)) * ramp_factor
        if abs(heading_error) > heading_tolerance_rad:
            forward_speed *= 0.25

        max_yaw_rate = get_config().navigation.max_yaw_rate
        yaw_rate = max(-max_yaw_rate, min(max_yaw_rate, heading_error * 1.5))
        return forward_speed, 0.0, yaw_rate

    def _compute_resume_ramp_factor(self) -> float:
        if self._resume_ramp_started_at is None or self._obstacle_resume_ramp_seconds <= 0:
            return 1.0
        elapsed = time.monotonic() - self._resume_ramp_started_at
        if elapsed >= self._obstacle_resume_ramp_seconds:
            self._resume_ramp_started_at = None
            return 1.0
        return elapsed / self._obstacle_resume_ramp_seconds

    async def _resolve_route_definition(
        self,
        origin_id: str,
        destination_id: str,
        waypoints: list[Waypoint],
    ) -> RouteDefinition:
        routes = await self._route_store.list_routes(active=True)
        for route in sorted(routes, key=lambda item: item.id):
            if route.origin_id == origin_id and route.destination_id == destination_id:
                return route

        return RouteDefinition(
            id=f"{origin_id}_TO_{destination_id}",
            name=f"{origin_id} to {destination_id}",
            origin_id=origin_id,
            destination_id=destination_id,
            waypoints=waypoints,
            active=True,
        )

    def _subscribe_navigation_events(self) -> None:
        event_bus = get_event_bus()
        self._subscription_ids = [
            event_bus.subscribe(EventName.HUMAN_CONFIRMED_LOAD, self._handle_human_confirmation),
            event_bus.subscribe(EventName.HUMAN_CONFIRMED_UNLOAD, self._handle_human_confirmation),
            event_bus.subscribe(EventName.OBSTACLE_DETECTED, self._handle_obstacle_detected),
            event_bus.subscribe(EventName.OBSTACLE_CLEARED, self._handle_obstacle_cleared),
        ]

    def _unsubscribe_navigation_events(self) -> None:
        event_bus = get_event_bus()
        for subscription_id in self._subscription_ids:
            event_bus.unsubscribe(subscription_id)
        self._subscription_ids = []

    async def _handle_human_confirmation(self, event: Any) -> None:
        if not self._is_navigating:
            return
        if not self._event_is_for_this_robot(event):
            return
        self._hold_event.set()

    async def _handle_obstacle_detected(self, event: Any) -> None:
        if not self._is_navigating:
            return
        if not self._event_is_for_this_robot(event):
            return
        # Cancel any pending stable-clear — obstacle is back.
        self._cancel_stable_clear_task()
        if not self._blocked:
            loop_time = asyncio.get_running_loop().time()
            self._blocked = True
            self._blocked_started_at = loop_time
            self._obstacle_detected_at = loop_time
            self._obstacle_count += 1
            if self._obstacle_count >= self._obstacle_repeat_fallback_count and not self._requires_hmi_confirmation:
                self._requires_hmi_confirmation = True
                logger.warning(
                    "Obstacle repeat threshold reached; manual CONFIRM_OBSTACLE_CLEARED required to resume",
                    extra={
                        "obstacle_count": self._obstacle_count,
                        "threshold": self._obstacle_repeat_fallback_count,
                        "route_id": self._current_route_id,
                    },
                )
        await self._clear_target_velocity("navigator_obstacle_detected")
        logger.warning("Navigation blocked by obstacle", extra={"route_id": self._current_route_id})

    async def _handle_obstacle_cleared(self, event: Any) -> None:
        if not self._is_navigating or not self._blocked:
            return
        if not self._event_is_for_this_robot(event):
            return

        payload = getattr(event, "payload", {}) or {}
        is_manual = payload.get("manual") is True

        if self._requires_hmi_confirmation and not is_manual:
            logger.info(
                "Obstacle cleared by sensor but manual HMI confirmation required; "
                "send CONFIRM_OBSTACLE_CLEARED to resume",
                extra={"obstacle_count": self._obstacle_count, "route_id": self._current_route_id},
            )
            return

        if is_manual:
            # Operator confirmed: clear immediately regardless of delay or repeat state.
            self._cancel_stable_clear_task()
            self._requires_hmi_confirmation = False
            self._apply_obstacle_clear(reason="manual_hmi")
            return

        # Sensor-auto path: treat a very brief detection as spurious.
        loop_time = asyncio.get_running_loop().time()
        hold_duration = (loop_time - self._obstacle_detected_at) if self._obstacle_detected_at is not None else float("inf")
        if hold_duration < self._obstacle_min_hold_seconds:
            # Undo the count increment so spurious flickers do not accumulate.
            self._obstacle_count = max(0, self._obstacle_count - 1)
            if self._requires_hmi_confirmation and self._obstacle_count < self._obstacle_repeat_fallback_count:
                self._requires_hmi_confirmation = False
            self._cancel_stable_clear_task()
            self._apply_obstacle_clear(reason="spurious")
            logger.debug(
                "Spurious obstacle cleared immediately",
                extra={"hold_duration_s": round(hold_duration, 3), "min_hold_s": self._obstacle_min_hold_seconds},
            )
            return

        # Real obstacle cleared: wait for the path to stay clear before resuming.
        self._cancel_stable_clear_task()
        self._stable_clear_task = asyncio.create_task(
            self._run_stable_clear_wait(), name="navigator-stable-clear"
        )

    async def _run_stable_clear_wait(self) -> None:
        try:
            await asyncio.sleep(self._obstacle_stable_clear_seconds)
        except asyncio.CancelledError:
            return
        if self._is_navigating and self._blocked:
            self._apply_obstacle_clear(reason="stable_clear")

    def _apply_obstacle_clear(self, reason: str) -> None:
        self._blocked = False
        self._blocked_started_at = None
        self._obstacle_detected_at = None
        if self._obstacle_resume_ramp_seconds > 0:
            self._resume_ramp_started_at = time.monotonic()
        self._publish_event(
            EventName.NAVIGATION_RESUMED,
            {"route_id": self._current_route_id, "task_id": self._active_task_id, "reason": reason},
            task_id=self._active_task_id,
        )
        logger.info(
            "Navigation resumed after obstacle",
            extra={"route_id": self._current_route_id, "reason": reason},
        )

    def _cancel_stable_clear_task(self) -> None:
        if self._stable_clear_task is not None and not self._stable_clear_task.done():
            self._stable_clear_task.cancel()
        self._stable_clear_task = None

    async def _clear_target_velocity(self, source: str) -> None:
        try:
            await self._heartbeat.clear_target_velocity(source=source)
        except Exception as exc:
            self._last_error = f"failed to clear heartbeat target: {exc}"
            logger.warning("Navigator failed to clear heartbeat target", extra={"source": source})

    def _raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise NavigationCancelledError(self._cancel_reason)

    def _event_is_for_this_robot(self, event: Any) -> bool:
        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            return True
        event_robot_id = payload.get("robot_id")
        if event_robot_id is None:
            return True
        return event_robot_id == self.robot_id

    def _publish_waypoint_arrival(
        self,
        route: RouteDefinition,
        waypoint: Waypoint,
        waypoint_index: int,
        task_id: str | None,
    ) -> None:
        self._publish_event(
            EventName.QUADRUPED_ARRIVED_AT_WAYPOINT,
            {
                **self._base_payload(route, task_id),
                "waypoint_name": waypoint.name,
                "waypoint_index": waypoint_index,
                "hold": waypoint.hold,
            },
            task_id=task_id,
        )

    def _publish_event(self, event_name: EventName, payload: dict[str, Any], *, task_id: str | None = None) -> None:
        try:
            enriched_payload = dict(payload)
            enriched_payload["robot_id"] = self.robot_id
            get_event_bus().publish_nowait(event_name, payload=enriched_payload, source=__name__, task_id=task_id)
        except asyncio.QueueFull:
            logger.warning("Navigator event bus queue full", extra={"event_name": event_name.value})
        except Exception:
            logger.exception("Navigator failed to publish event", extra={"event_name": event_name.value})

    def _base_payload(self, route: RouteDefinition, task_id: str | None) -> dict[str, Any]:
        return {
            "route_id": route.id,
            "origin_id": route.origin_id,
            "destination_id": route.destination_id,
            "task_id": task_id,
        }

    def _result_payload(self, result: NavigationResult) -> dict[str, Any]:
        return {
            "success": result.success,
            "route_id": result.route_id,
            "origin_id": result.origin_id,
            "destination_id": result.destination_id,
            "completed_waypoints": result.completed_waypoints,
            "total_waypoints": result.total_waypoints,
            "blocked": result.blocked,
            "cancelled": result.cancelled,
            "message": result.message,
        }


navigator = Navigator()


def get_navigator() -> Navigator:
    return navigator


__all__ = [
    "NavigationBlockedError",
    "NavigationCancelledError",
    "NavigationResult",
    "Navigator",
    "NavigatorError",
    "_normalize_angle_rad",
    "get_navigator",
    "navigator",
]
