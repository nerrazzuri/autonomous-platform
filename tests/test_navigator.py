from __future__ import annotations

import asyncio
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_state(x: float, y: float, *, yaw: float = 0.0, connection_ok: bool = True):
    from quadruped.sdk_adapter import QuadrupedMode
    from quadruped.state_monitor import QuadrupedState

    return QuadrupedState(
        timestamp=datetime.now(timezone.utc),
        battery_pct=100,
        position=(x, y, 0.0),
        rpy=(0.0, 0.0, yaw),
        control_mode=0,
        connection_ok=connection_ok,
        mode=QuadrupedMode.STANDING,
    )


class FakeRouteStore:
    def __init__(self, routes=None):
        self.routes = {route.id: route for route in (routes or [])}

    async def get_route(self, origin_id: str, destination_id: str):
        from navigation.route_store import RouteNotFoundError

        matches = [
            route
            for route in self.routes.values()
            if route.active and route.origin_id == origin_id and route.destination_id == destination_id
        ]
        if not matches:
            raise RouteNotFoundError(f"Active route not found for {origin_id} -> {destination_id}")
        return sorted(matches, key=lambda route: route.id)[0].waypoints

    async def get_route_definition(self, route_id: str):
        from navigation.route_store import RouteNotFoundError

        route = self.routes.get(route_id)
        if route is None:
            raise RouteNotFoundError(f"Route not found: {route_id}")
        return route

    async def list_routes(self, active=None):
        routes = list(self.routes.values())
        if active is not None:
            routes = [route for route in routes if route.active is active]
        return sorted(routes, key=lambda route: route.id)


class FakeStateMonitor:
    def __init__(self, states=None):
        self.states = list(states or [])
        self.index = 0
        self.poll_calls = 0

    async def get_current_state(self):
        if not self.states:
            return None
        state = self.states[min(self.index, len(self.states) - 1)]
        if self.index < len(self.states) - 1:
            self.index += 1
        return state

    async def poll_once(self):
        self.poll_calls += 1
        return await self.get_current_state()


class FakeHeartbeat:
    def __init__(self):
        self.commands = []
        self.cleared = 0

    async def set_target_velocity(self, vx, vy, yaw_rate, **kwargs):
        self.commands.append((vx, vy, yaw_rate, kwargs))

    async def clear_target_velocity(self, **kwargs):
        self.cleared += 1
        self.commands.append((0.0, 0.0, 0.0, kwargs))


class FakeSDKAdapter:
    pass


class FakeSLAMProvider:
    def __init__(self, corrected_position):
        self.corrected_position = corrected_position
        self.calls = 0

    async def get_corrected_position(self):
        self.calls += 1
        return self.corrected_position


class FakePoseBridge:
    def __init__(self, pose):
        self.pose = pose

    def get_latest_pose(self):
        return self.pose


def make_route(route_id="LINE_A_TO_QA", *, hold=False, target=(1.0, 0.0)):
    from navigation.route_store import RouteDefinition, Waypoint

    return RouteDefinition(
        id=route_id,
        name=route_id.replace("_", " "),
        origin_id="LINE_A",
        destination_id="QA",
        waypoints=[
            Waypoint(
                name="target",
                x=target[0],
                y=target[1],
                heading_deg=0.0,
                velocity=0.25,
                hold=hold,
            )
        ],
    )


@pytest_asyncio.fixture
async def navigator_env(monkeypatch: pytest.MonkeyPatch):
    from core.event_bus import EventBus
    import navigation.navigator as navigator_module

    event_bus = EventBus()
    await event_bus.start()
    monkeypatch.setattr(navigator_module, "get_event_bus", lambda: event_bus)
    yield navigator_module, event_bus
    await event_bus.stop()


@pytest.mark.asyncio
async def test_execute_route_completes_simple_route(navigator_env) -> None:
    navigator_module, _ = navigator_env
    route = make_route()
    state_monitor = FakeStateMonitor([make_state(0.0, 0.0), make_state(0.6, 0.0), make_state(1.0, 0.0)])
    heartbeat = FakeHeartbeat()
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([route]),
        state_monitor=state_monitor,
        heartbeat=heartbeat,
        waypoint_tolerance_m=0.1,
    )

    result = await navigator.execute_route("LINE_A", "QA", task_id="task-1")

    assert result.success is True
    assert result.route_id == "LINE_A_TO_QA"
    assert result.completed_waypoints == 1
    assert any(command[0] > 0 for command in heartbeat.commands)
    assert heartbeat.cleared >= 1


def test_constructor_accepts_sdk_adapter_and_robot_id() -> None:
    import navigation.navigator as navigator_module

    sdk_adapter = FakeSDKAdapter()
    navigator = navigator_module.Navigator(
        sdk_adapter=sdk_adapter,
        route_store=FakeRouteStore([make_route()]),
        state_monitor=FakeStateMonitor([make_state(0.0, 0.0)]),
        heartbeat=FakeHeartbeat(),
        robot_id="robot-7",
    )

    assert navigator.robot_id == "robot-7"
    assert navigator._sdk_adapter is sdk_adapter


def test_constructor_rejects_empty_robot_id() -> None:
    import navigation.navigator as navigator_module

    with pytest.raises(navigator_module.NavigatorError, match="robot_id"):
        navigator_module.Navigator(
            route_store=FakeRouteStore([make_route()]),
            state_monitor=FakeStateMonitor([make_state(0.0, 0.0)]),
            heartbeat=FakeHeartbeat(),
            robot_id="",
        )


def test_default_robot_id_is_default() -> None:
    import navigation.navigator as navigator_module

    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([make_route()]),
        state_monitor=FakeStateMonitor([make_state(0.0, 0.0)]),
        heartbeat=FakeHeartbeat(),
    )

    assert navigator.robot_id == "default"


@pytest.mark.asyncio
async def test_execute_route_by_id(navigator_env) -> None:
    navigator_module, _ = navigator_env
    route = make_route(route_id="ROUTE_BY_ID", target=(0.0, 0.0))
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([route]),
        state_monitor=FakeStateMonitor([make_state(0.0, 0.0)]),
        heartbeat=FakeHeartbeat(),
        waypoint_tolerance_m=0.1,
    )

    result = await navigator.execute_route_by_id("ROUTE_BY_ID")

    assert result.success is True
    assert result.route_id == "ROUTE_BY_ID"


@pytest.mark.asyncio
async def test_execute_route_rejects_concurrent_navigation(navigator_env) -> None:
    navigator_module, _ = navigator_env
    route = make_route(hold=True, target=(0.0, 0.0))
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([route]),
        state_monitor=FakeStateMonitor([make_state(0.0, 0.0)]),
        heartbeat=FakeHeartbeat(),
        waypoint_tolerance_m=0.1,
    )
    running = asyncio.create_task(navigator.execute_route("LINE_A", "QA"))
    await asyncio.sleep(0.02)

    with pytest.raises(navigator_module.NavigatorError):
        await navigator.execute_route("LINE_A", "QA")

    await navigator.cancel_navigation()
    await running


@pytest.mark.asyncio
async def test_unknown_route_raises(navigator_env) -> None:
    navigator_module, _ = navigator_env
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([]),
        state_monitor=FakeStateMonitor([make_state(0.0, 0.0)]),
        heartbeat=FakeHeartbeat(),
    )

    from navigation.route_store import RouteNotFoundError

    with pytest.raises(RouteNotFoundError):
        await navigator.execute_route("LINE_A", "QA")


@pytest.mark.asyncio
async def test_navigation_fails_when_no_state_available(navigator_env) -> None:
    navigator_module, _ = navigator_env
    route = make_route()
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([route]),
        state_monitor=FakeStateMonitor([]),
        heartbeat=FakeHeartbeat(),
    )

    result = await navigator.execute_route("LINE_A", "QA")

    assert result.success is False
    assert "state" in result.message.lower()


@pytest.mark.asyncio
async def test_get_state_uses_slam_corrected_position_when_configured(monkeypatch, navigator_env) -> None:
    navigator_module, _ = navigator_env
    from shared.navigation.slam import CorrectedPosition

    config = navigator_module.get_config()
    monkeypatch.setattr(
        navigator_module,
        "get_config",
        lambda: config.model_copy(update={"navigation": config.navigation.model_copy(update={"position_source": "slam"})}),
    )
    slam_provider = FakeSLAMProvider(CorrectedPosition(x=3.25, y=4.5, heading_rad=1.2, source="slam_toolbox", confidence=0.9))
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([make_route()]),
        state_monitor=FakeStateMonitor([make_state(0.0, 0.0, yaw=0.1)]),
        heartbeat=FakeHeartbeat(),
        slam_provider=slam_provider,
    )

    state = await navigator._get_state_or_poll()

    assert state is not None
    assert state.position == (3.25, 4.5, 0.0)
    assert state.rpy == (0.0, 0.0, 1.2)
    assert slam_provider.calls == 1


@pytest.mark.asyncio
async def test_get_state_enables_real_slam_provider_from_config(monkeypatch, navigator_env) -> None:
    navigator_module, _ = navigator_env
    import shared.ros2 as ros2_module
    import shared.navigation.slam as slam_module
    from shared.navigation.slam import SLAMProvider

    config = navigator_module.get_config()
    monkeypatch.setattr(
        navigator_module,
        "get_config",
        lambda: config.model_copy(update={"navigation": config.navigation.model_copy(update={"position_source": "slam"})}),
    )
    monkeypatch.setattr(
        slam_module,
        "slam_provider",
        SLAMProvider(state_monitor=FakeStateMonitor([make_state(0.0, 0.0, yaw=0.0)]), enabled=False),
    )
    pose = SimpleNamespace(
        pose=SimpleNamespace(
            pose=SimpleNamespace(
                position=SimpleNamespace(x=8.0, y=9.0),
                orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
            ),
            covariance=[0.0] * 36,
        )
    )
    monkeypatch.setattr(ros2_module, "_bridge", FakePoseBridge(pose))
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([make_route()]),
        state_monitor=FakeStateMonitor([make_state(1.0, 2.0, yaw=0.5)]),
        heartbeat=FakeHeartbeat(),
    )

    state = await navigator._get_state_or_poll()

    assert state is not None
    assert state.position == (8.0, 9.0, 0.0)
    assert state.rpy == (0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_get_state_slam_config_falls_back_to_odometry_without_pose(monkeypatch, navigator_env) -> None:
    navigator_module, _ = navigator_env
    import shared.ros2 as ros2_module

    config = navigator_module.get_config()
    monkeypatch.setattr(
        navigator_module,
        "get_config",
        lambda: config.model_copy(update={"navigation": config.navigation.model_copy(update={"position_source": "slam"})}),
    )
    monkeypatch.setattr(ros2_module, "_bridge", None)
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([make_route()]),
        state_monitor=FakeStateMonitor([make_state(1.0, 2.0, yaw=0.5)]),
        heartbeat=FakeHeartbeat(),
    )

    state = await navigator._get_state_or_poll()

    assert state is not None
    assert state.position == (1.0, 2.0, 0.0)
    assert state.rpy == (0.0, 0.0, 0.5)


@pytest.mark.asyncio
async def test_waypoint_hold_waits_for_confirmation_event(navigator_env) -> None:
    navigator_module, event_bus = navigator_env
    route = make_route(hold=True, target=(0.0, 0.0))
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([route]),
        state_monitor=FakeStateMonitor([make_state(0.0, 0.0)]),
        heartbeat=FakeHeartbeat(),
        waypoint_tolerance_m=0.1,
    )

    task = asyncio.create_task(navigator.execute_route("LINE_A", "QA", task_id="task-hold"))
    await asyncio.sleep(0.05)

    assert task.done() is False

    from core.event_bus import EventName

    await event_bus.publish(EventName.HUMAN_CONFIRMED_LOAD, {"task_id": "task-hold"})
    await event_bus.wait_until_idle(timeout=1.0)
    result = await asyncio.wait_for(task, timeout=1.0)

    assert result.success is True


@pytest.mark.asyncio
async def test_robot_scoped_human_confirmation_ignores_other_robot(navigator_env) -> None:
    navigator_module, event_bus = navigator_env
    route = make_route(hold=True, target=(0.0, 0.0))
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([route]),
        state_monitor=FakeStateMonitor([make_state(0.0, 0.0)]),
        heartbeat=FakeHeartbeat(),
        waypoint_tolerance_m=0.1,
        robot_id="robot-1",
    )

    task = asyncio.create_task(navigator.execute_route("LINE_A", "QA", task_id="task-hold"))
    await asyncio.sleep(0.05)

    from core.event_bus import EventName

    await event_bus.publish(EventName.HUMAN_CONFIRMED_LOAD, {"task_id": "task-hold", "robot_id": "robot-2"})
    await event_bus.wait_until_idle(timeout=1.0)
    await asyncio.sleep(0.05)

    assert task.done() is False

    await event_bus.publish(EventName.HUMAN_CONFIRMED_LOAD, {"task_id": "task-hold", "robot_id": "robot-1"})
    await event_bus.wait_until_idle(timeout=1.0)
    result = await asyncio.wait_for(task, timeout=1.0)

    assert result.success is True


@pytest.mark.asyncio
async def test_cancel_navigation_stops_route(navigator_env) -> None:
    navigator_module, _ = navigator_env
    route = make_route(hold=True, target=(0.0, 0.0))
    heartbeat = FakeHeartbeat()
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([route]),
        state_monitor=FakeStateMonitor([make_state(0.0, 0.0)]),
        heartbeat=heartbeat,
        waypoint_tolerance_m=0.1,
    )
    task = asyncio.create_task(navigator.execute_route("LINE_A", "QA"))
    await asyncio.sleep(0.02)

    await navigator.cancel_navigation("test cancel")
    result = await asyncio.wait_for(task, timeout=1.0)

    assert result.cancelled is True
    assert result.success is False
    assert heartbeat.cleared >= 1


@pytest.mark.asyncio
async def test_obstacle_detected_blocks_motion(navigator_env) -> None:
    navigator_module, event_bus = navigator_env
    route = make_route(target=(5.0, 0.0))
    heartbeat = FakeHeartbeat()
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([route]),
        state_monitor=FakeStateMonitor([make_state(0.0, 0.0)]),
        heartbeat=heartbeat,
        waypoint_tolerance_m=0.1,
        obstacle_hold_timeout_seconds=1.0,
    )
    task = asyncio.create_task(navigator.execute_route("LINE_A", "QA"))
    await asyncio.sleep(0.02)

    from core.event_bus import EventName

    await event_bus.publish(EventName.OBSTACLE_DETECTED, {})
    await event_bus.wait_until_idle(timeout=1.0)

    assert heartbeat.cleared >= 1

    await navigator.cancel_navigation()
    await task


@pytest.mark.asyncio
async def test_obstacle_cleared_resumes_navigation(navigator_env) -> None:
    navigator_module, event_bus = navigator_env
    route = make_route(target=(1.0, 0.0))
    state_monitor = FakeStateMonitor([make_state(0.0, 0.0), make_state(0.0, 0.0), make_state(1.0, 0.0)])
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([route]),
        state_monitor=state_monitor,
        heartbeat=FakeHeartbeat(),
        waypoint_tolerance_m=0.1,
        obstacle_hold_timeout_seconds=1.0,
    )
    resumed_events = []
    from core.event_bus import EventName

    event_bus.subscribe(EventName.NAVIGATION_RESUMED, lambda event: resumed_events.append(event))
    task = asyncio.create_task(navigator.execute_route("LINE_A", "QA"))
    await asyncio.sleep(0.02)
    await event_bus.publish(EventName.OBSTACLE_DETECTED, {})
    await event_bus.wait_until_idle(timeout=1.0)
    await asyncio.sleep(0.02)
    await event_bus.publish(EventName.OBSTACLE_CLEARED, {})
    await event_bus.wait_until_idle(timeout=1.0)

    result = await asyncio.wait_for(task, timeout=1.0)

    assert result.success is True
    assert resumed_events
    assert resumed_events[0].payload["robot_id"] == "default"


@pytest.mark.asyncio
async def test_robot_scoped_obstacle_event_ignores_other_robot(navigator_env) -> None:
    navigator_module, event_bus = navigator_env
    route = make_route(target=(1.0, 0.0))
    state_monitor = FakeStateMonitor([make_state(0.0, 0.0), make_state(1.0, 0.0)])
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([route]),
        state_monitor=state_monitor,
        heartbeat=FakeHeartbeat(),
        waypoint_tolerance_m=0.1,
        obstacle_hold_timeout_seconds=0.2,
        robot_id="robot-1",
    )

    from core.event_bus import EventName

    task = asyncio.create_task(navigator.execute_route("LINE_A", "QA"))
    await asyncio.sleep(0.02)
    await event_bus.publish(EventName.OBSTACLE_DETECTED, {"robot_id": "robot-2"})
    await event_bus.wait_until_idle(timeout=1.0)

    result = await asyncio.wait_for(task, timeout=1.0)

    assert result.success is True


@pytest.mark.asyncio
async def test_obstacle_timeout_returns_blocked_result(navigator_env) -> None:
    navigator_module, event_bus = navigator_env
    route = make_route(target=(5.0, 0.0))
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([route]),
        state_monitor=FakeStateMonitor([make_state(0.0, 0.0)]),
        heartbeat=FakeHeartbeat(),
        waypoint_tolerance_m=0.1,
        obstacle_hold_timeout_seconds=0.05,
    )
    from core.event_bus import EventName

    task = asyncio.create_task(navigator.execute_route("LINE_A", "QA"))
    await asyncio.sleep(0.02)
    await event_bus.publish(EventName.OBSTACLE_DETECTED, {})
    await event_bus.wait_until_idle(timeout=1.0)

    result = await asyncio.wait_for(task, timeout=1.0)

    assert result.success is False
    assert result.blocked is True
    assert "obstacle" in result.message.lower()


@pytest.mark.asyncio
async def test_heartbeat_target_cleared_on_exit(navigator_env) -> None:
    navigator_module, _ = navigator_env
    route = make_route(target=(0.0, 0.0))
    heartbeat = FakeHeartbeat()
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([route]),
        state_monitor=FakeStateMonitor([make_state(0.0, 0.0)]),
        heartbeat=heartbeat,
        waypoint_tolerance_m=0.1,
    )

    await navigator.execute_route("LINE_A", "QA")

    assert heartbeat.commands[-1][0:3] == (0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_published_events_include_robot_id(navigator_env) -> None:
    navigator_module, event_bus = navigator_env
    route = make_route(target=(0.0, 0.0))
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([route]),
        state_monitor=FakeStateMonitor([make_state(0.0, 0.0)]),
        heartbeat=FakeHeartbeat(),
        waypoint_tolerance_m=0.1,
        robot_id="robot-9",
    )
    received = []

    def callback(event):
        received.append(event)

    from core.event_bus import EventName

    event_bus.subscribe(EventName.NAVIGATION_STARTED, callback, subscriber_name="test-nav-started")
    event_bus.subscribe(
        EventName.QUADRUPED_ARRIVED_AT_WAYPOINT,
        callback,
        subscriber_name="test-waypoint-arrival",
    )
    event_bus.subscribe(EventName.NAVIGATION_COMPLETED, callback, subscriber_name="test-nav-completed")

    result = await navigator.execute_route("LINE_A", "QA", task_id="task-1")
    await event_bus.wait_until_idle(timeout=1.0)

    assert result.success is True
    assert [event.name for event in received] == [
        EventName.NAVIGATION_STARTED,
        EventName.QUADRUPED_ARRIVED_AT_WAYPOINT,
        EventName.NAVIGATION_COMPLETED,
    ]
    assert all(event.payload["robot_id"] == "robot-9" for event in received)


@pytest.mark.asyncio
async def test_current_route_id_and_completed_waypoint_count(navigator_env) -> None:
    navigator_module, _ = navigator_env
    route = make_route(hold=True, target=(0.0, 0.0))
    navigator = navigator_module.Navigator(
        route_store=FakeRouteStore([route]),
        state_monitor=FakeStateMonitor([make_state(0.0, 0.0)]),
        heartbeat=FakeHeartbeat(),
        waypoint_tolerance_m=0.1,
    )
    task = asyncio.create_task(navigator.execute_route("LINE_A", "QA"))
    await asyncio.sleep(0.05)

    assert navigator.current_route_id() == "LINE_A_TO_QA"
    assert navigator.completed_waypoint_count() == 1

    await navigator.cancel_navigation()
    await task


def test_global_get_navigator_returns_navigator() -> None:
    from navigation.navigator import Navigator, get_navigator

    assert isinstance(get_navigator(), Navigator)


def test_normalize_angle_rad() -> None:
    from navigation.navigator import _normalize_angle_rad

    assert math.isclose(_normalize_angle_rad(3 * math.pi), math.pi)
    assert math.isclose(_normalize_angle_rad(-3 * math.pi), -math.pi)
    assert math.isclose(_normalize_angle_rad(0.5), 0.5)
