from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeCycle:
    def __init__(
        self,
        cycle_id: str = "cycle-1",
        route_id: str = "PATROL_NORTH_LOOP",
        triggered_by: str = "schedule",
        status: str = "scheduled",
        robot_id: str | None = None,
    ) -> None:
        self.cycle_id = cycle_id
        self.route_id = route_id
        self.triggered_by = triggered_by
        self.status = status
        self.robot_id = robot_id


class FakePatrolQueue:
    def __init__(self) -> None:
        self.next_cycle: FakeCycle | None = None
        self.next_cycles: list[FakeCycle] = []
        self.requested_robot_ids: list[str | None] = []
        self.active_calls: list[str] = []
        self.completed_calls: list[tuple[str, dict[str, object]]] = []
        self.failed_calls: list[tuple[str, str]] = []

    async def get_next_cycle(self, robot_id: str | None = None):
        self.requested_robot_ids.append(robot_id)
        if self.next_cycles:
            for index, cycle in enumerate(self.next_cycles):
                cycle_robot_id = getattr(cycle, "robot_id", None)
                if cycle_robot_id is None or robot_id is None or cycle_robot_id == robot_id:
                    return self.next_cycles.pop(index)
            return None
        if self.next_cycle is not None:
            cycle_robot_id = getattr(self.next_cycle, "robot_id", None)
            if cycle_robot_id is not None and robot_id is not None and cycle_robot_id != robot_id:
                return None
        cycle = self.next_cycle
        self.next_cycle = None
        return cycle

    async def mark_active(self, cycle_id: str):
        self.active_calls.append(cycle_id)
        return FakeCycle(cycle_id=cycle_id, status="active")

    async def mark_completed(self, cycle_id: str, stats_dict: dict[str, object] | None = None):
        stats = dict(stats_dict or {})
        self.completed_calls.append((cycle_id, stats))
        return FakeCycle(cycle_id=cycle_id, status="completed")

    async def mark_failed(self, cycle_id: str, reason: str):
        self.failed_calls.append((cycle_id, reason))
        return FakeCycle(cycle_id=cycle_id, status="failed")


class FakeObserver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.results: list[SimpleNamespace] = []

    async def observe(self, waypoint_name: str, zone_id: str, cycle_id: str):
        self.calls.append((waypoint_name, zone_id, cycle_id))
        if self.results:
            return self.results.pop(0)
        return SimpleNamespace(anomaly_id=None)


class FakeNavigator:
    def __init__(self, result=None) -> None:
        from shared.navigation.navigator import NavigationResult

        self.busy = False
        self.calls: list[tuple[str, str | None]] = []
        self.cancel_calls: list[str] = []
        self.release_event: asyncio.Event | None = None
        self.started_event = asyncio.Event()
        self.on_execute = None
        self.route_results = {
            "PATROL_NORTH_LOOP": result
            or NavigationResult(
                success=True,
                route_id="PATROL_NORTH_LOOP",
                origin_id="A",
                destination_id="B",
                completed_waypoints=3,
                total_waypoints=3,
                message="done",
            )
        }

    def is_navigating(self) -> bool:
        return self.busy

    async def execute_route_by_id(self, route_id: str, *, task_id: str | None = None):
        self.busy = True
        self.calls.append((route_id, task_id))
        self.started_event.set()
        try:
            if self.on_execute is not None:
                await self.on_execute(route_id, task_id)
            if self.release_event is not None:
                await self.release_event.wait()
            result = self.route_results.get(route_id)
            if isinstance(result, Exception):
                raise result
            if result is None:
                raise RuntimeError(f"missing route: {route_id}")
            return result
        finally:
            self.busy = False

    async def cancel_navigation(self, reason: str = "cancelled") -> None:
        self.cancel_calls.append(reason)
        if self.release_event is not None:
            self.release_event.set()
        route_id, task_id = self.calls[-1]
        result = self.route_results.get(route_id)
        if result is not None:
            self.route_results[route_id] = SimpleNamespace(
                **{
                    **result.__dict__,
                    "success": False,
                    "cancelled": True,
                    "blocked": False,
                    "message": reason,
                }
            )


class FakeEventBus:
    def __init__(self) -> None:
        self._subscriptions: dict[str, tuple[object, object]] = {}
        self._published = []
        self._started = False
        self._counter = 0

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    def subscribe(self, event_name, callback, *, subscriber_name: str | None = None) -> str:
        self._counter += 1
        subscription_id = f"sub-{self._counter}"
        self._subscriptions[subscription_id] = (event_name, callback)
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        return self._subscriptions.pop(subscription_id, None) is not None

    async def publish(self, event_name, payload: dict[str, object] | None = None, *, task_id: str | None = None, **_kwargs):
        event = SimpleNamespace(name=event_name, payload=dict(payload or {}), task_id=task_id)
        self._published.append(event)
        for subscribed_name, callback in list(self._subscriptions.values()):
            if subscribed_name == event_name:
                result = callback(event)
                if asyncio.iscoroutine(result):
                    await result
        return event

    async def wait_until_idle(self, timeout: float | None = None) -> None:
        return None

    def subscriber_count(self, event_name=None) -> int:
        if event_name is None:
            return len(self._subscriptions)
        return sum(1 for subscribed_name, _callback in self._subscriptions.values() if subscribed_name == event_name)

    @property
    def published(self):
        return list(self._published)


class FakeRobotRegistry:
    def __init__(self, *platforms) -> None:
        self._platforms = {platform.robot_id: platform for platform in platforms}

    def get(self, robot_id: str):
        if robot_id not in self._platforms:
            from shared.quadruped.robot_registry import RobotNotFoundError

            raise RobotNotFoundError(robot_id)
        return self._platforms[robot_id]

    def all(self):
        return list(self._platforms.values())


def make_platform(robot_id: str, *, navigator=None, role: str | None = "patrol"):
    return SimpleNamespace(
        robot_id=robot_id,
        navigator=navigator or FakeNavigator(),
        config=SimpleNamespace(
            role=role,
            connection=SimpleNamespace(role=role),
        ),
    )


def make_nav_result(
    *,
    success: bool,
    total_waypoints: int = 3,
    blocked: bool = False,
    cancelled: bool = False,
    message: str = "",
):
    from shared.navigation.navigator import NavigationResult

    return NavigationResult(
        success=success,
        route_id="PATROL_NORTH_LOOP",
        origin_id="A",
        destination_id="B",
        completed_waypoints=0 if not success else total_waypoints,
        total_waypoints=total_waypoints,
        blocked=blocked,
        cancelled=cancelled,
        message=message,
    )


@pytest.fixture
def dispatcher_module():
    return importlib.import_module("apps.patrol.tasks.patrol_dispatcher")


def build_dispatcher(dispatcher_module, **kwargs):
    return dispatcher_module.PatrolDispatcher(
        patrol_queue=kwargs.pop("patrol_queue", FakePatrolQueue()),
        navigator=kwargs.pop("navigator", FakeNavigator()),
        observer=kwargs.pop("observer", FakeObserver()),
        event_bus=kwargs.pop("event_bus", FakeEventBus()),
        poll_interval_seconds=kwargs.pop("poll_interval_seconds", 60.0),
        dock_route_id=kwargs.pop("dock_route_id", "PATROL_TO_DOCK"),
        robot_registry=kwargs.pop("robot_registry", None),
    )


async def wait_for_condition(predicate, *, timeout: float = 1.0) -> None:
    async def _wait() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait(), timeout=timeout)


def test_invalid_poll_interval_rejected(dispatcher_module) -> None:
    with pytest.raises(dispatcher_module.PatrolDispatcherError, match="poll_interval_seconds"):
        build_dispatcher(dispatcher_module, poll_interval_seconds=0)


def test_empty_dock_route_rejected(dispatcher_module) -> None:
    with pytest.raises(dispatcher_module.PatrolDispatcherError, match="dock_route_id"):
        build_dispatcher(dispatcher_module, dock_route_id="")


@pytest.mark.asyncio
async def test_dispatch_once_returns_false_when_suspended(dispatcher_module) -> None:
    dispatcher = build_dispatcher(dispatcher_module)

    await dispatcher.suspend("manual")

    assert await dispatcher.dispatch_once() is False


@pytest.mark.asyncio
async def test_dispatch_once_returns_false_when_navigator_busy(dispatcher_module) -> None:
    queue = FakePatrolQueue()
    queue.next_cycle = FakeCycle()
    navigator = FakeNavigator()
    navigator.busy = True
    dispatcher = build_dispatcher(dispatcher_module, patrol_queue=queue, navigator=navigator)

    assert await dispatcher.dispatch_once() is False


@pytest.mark.asyncio
async def test_dispatch_once_returns_false_when_no_cycle(dispatcher_module) -> None:
    dispatcher = build_dispatcher(dispatcher_module)

    assert await dispatcher.dispatch_once() is False


@pytest.mark.asyncio
async def test_dispatch_once_executes_cycle_success(dispatcher_module) -> None:
    queue = FakePatrolQueue()
    queue.next_cycle = FakeCycle(cycle_id="cycle-1", route_id="PATROL_NORTH_LOOP")
    navigator = FakeNavigator(result=make_nav_result(success=True, total_waypoints=3))
    navigator.route_results["PATROL_TO_DOCK"] = make_nav_result(success=True, total_waypoints=1)
    dispatcher = build_dispatcher(dispatcher_module, patrol_queue=queue, navigator=navigator)

    processed = await dispatcher.dispatch_once()
    state = await dispatcher.get_state()

    assert processed is True
    assert queue.active_calls == ["cycle-1"]
    assert queue.completed_calls == [
        (
            "cycle-1",
            {
                "waypoints_total": 3,
                "waypoints_observed": 0,
                "anomaly_ids": [],
            },
        )
    ]
    assert navigator.calls == [("PATROL_NORTH_LOOP", "cycle-1"), ("PATROL_TO_DOCK", None)]
    assert state.active_cycle_id is None
    assert state.active_route_id is None
    assert state.consecutive_failures == 0


@pytest.mark.asyncio
async def test_dispatcher_observes_waypoint_events_with_metadata(dispatcher_module) -> None:
    queue = FakePatrolQueue()
    queue.next_cycle = FakeCycle(cycle_id="cycle-1")
    event_bus = FakeEventBus()
    observer = FakeObserver()
    observer.results = [SimpleNamespace(anomaly_id="anom-1"), SimpleNamespace(anomaly_id=None)]
    navigator = FakeNavigator(result=make_nav_result(success=True, total_waypoints=4))

    async def publish_waypoints(route_id: str, task_id: str | None) -> None:
        assert route_id == "PATROL_NORTH_LOOP"
        await event_bus.publish(
            dispatcher_module.EventName.QUADRUPED_ARRIVED_AT_WAYPOINT,
            {
                "waypoint_name": "alpha",
                "metadata": {"observe": True, "zone_id": "zone-a"},
            },
            task_id=task_id,
        )
        await event_bus.publish(
            dispatcher_module.EventName.QUADRUPED_ARRIVED_AT_WAYPOINT,
            {
                "waypoint_name": "beta",
                "metadata": {"observe": False, "zone_id": "zone-b"},
            },
            task_id=task_id,
        )
        await event_bus.publish(
            dispatcher_module.EventName.QUADRUPED_ARRIVED_AT_WAYPOINT,
            {
                "waypoint_name": "gamma",
                "metadata": {"observe": True},
            },
            task_id=task_id,
        )
        await event_bus.publish(
            dispatcher_module.EventName.QUADRUPED_ARRIVED_AT_WAYPOINT,
            {
                "waypoint_name": "delta",
                "metadata": {"observe": True, "zone_id": "zone-d"},
            },
            task_id=task_id,
        )

    navigator.on_execute = publish_waypoints
    dispatcher = build_dispatcher(
        dispatcher_module,
        patrol_queue=queue,
        navigator=navigator,
        observer=observer,
        event_bus=event_bus,
    )
    await dispatcher.start()

    try:
        processed = await dispatcher.dispatch_once()
    finally:
        await dispatcher.stop()

    assert processed is True
    assert observer.calls == [("alpha", "zone-a", "cycle-1"), ("delta", "zone-d", "cycle-1")]
    assert queue.completed_calls == [
        (
            "cycle-1",
            {
                "waypoints_total": 4,
                "waypoints_observed": 2,
                "anomaly_ids": ["anom-1"],
            },
        )
    ]


@pytest.mark.asyncio
async def test_dispatcher_ignores_waypoint_events_for_other_cycle(dispatcher_module) -> None:
    queue = FakePatrolQueue()
    queue.next_cycle = FakeCycle(cycle_id="cycle-1")
    event_bus = FakeEventBus()
    observer = FakeObserver()
    navigator = FakeNavigator(result=make_nav_result(success=True, total_waypoints=1))

    async def publish_waypoint_for_other_cycle(route_id: str, task_id: str | None) -> None:
        assert route_id == "PATROL_NORTH_LOOP"
        await event_bus.publish(
            dispatcher_module.EventName.QUADRUPED_ARRIVED_AT_WAYPOINT,
            {
                "waypoint_name": "other",
                "metadata": {"observe": True, "zone_id": "zone-x"},
                "task_id": "other-cycle",
            },
            task_id=task_id,
        )

    navigator.on_execute = publish_waypoint_for_other_cycle
    dispatcher = build_dispatcher(
        dispatcher_module,
        patrol_queue=queue,
        navigator=navigator,
        observer=observer,
        event_bus=event_bus,
    )
    await dispatcher.start()

    try:
        processed = await dispatcher.dispatch_once()
    finally:
        await dispatcher.stop()

    assert processed is True
    assert observer.calls == []
    assert queue.completed_calls[0][1]["waypoints_observed"] == 0


@pytest.mark.asyncio
async def test_navigation_failure_marks_cycle_failed(dispatcher_module) -> None:
    queue = FakePatrolQueue()
    queue.next_cycle = FakeCycle(cycle_id="cycle-1")
    navigator = FakeNavigator(result=make_nav_result(success=False, blocked=True, message="obstacle timeout"))
    event_bus = FakeEventBus()
    dispatcher = build_dispatcher(dispatcher_module, patrol_queue=queue, navigator=navigator, event_bus=event_bus)

    processed = await dispatcher.dispatch_once()
    state = await dispatcher.get_state()

    assert processed is True
    assert queue.failed_calls == [("cycle-1", "obstacle timeout")]
    assert state.consecutive_failures == 1
    assert any(event.name == dispatcher_module.EventName.PATROL_CYCLE_FAILED for event in event_bus.published)


@pytest.mark.asyncio
async def test_consecutive_failures_trigger_suspension(dispatcher_module, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = FakePatrolQueue()
    navigator = FakeNavigator(result=make_nav_result(success=False, message="route failed"))
    event_bus = FakeEventBus()
    dispatcher = build_dispatcher(dispatcher_module, patrol_queue=queue, navigator=navigator, event_bus=event_bus)

    monkeypatch.setattr(
        dispatcher_module,
        "get_config",
        lambda: SimpleNamespace(patrol=SimpleNamespace(max_consecutive_failures=2)),
    )

    queue.next_cycle = FakeCycle(cycle_id="cycle-1")
    assert await dispatcher.dispatch_once() is True
    queue.next_cycle = FakeCycle(cycle_id="cycle-2")
    assert await dispatcher.dispatch_once() is True

    state = await dispatcher.get_state()

    assert state.suspended is True
    assert state.consecutive_failures == 2
    assert any(event.name == dispatcher_module.EventName.PATROL_SUSPENDED for event in event_bus.published)


@pytest.mark.asyncio
async def test_suspend_and_resume_methods(dispatcher_module) -> None:
    event_bus = FakeEventBus()
    dispatcher = build_dispatcher(dispatcher_module, event_bus=event_bus)

    await dispatcher.suspend("manual hold")
    assert dispatcher.is_suspended() is True

    await dispatcher.resume("manual resume")
    state = await dispatcher.get_state()

    assert state.suspended is False
    assert [event.name for event in event_bus.published] == [
        dispatcher_module.EventName.PATROL_SUSPENDED,
        dispatcher_module.EventName.PATROL_RESUMED,
    ]


@pytest.mark.asyncio
async def test_estop_suspends_and_cancels_navigation(dispatcher_module) -> None:
    queue = FakePatrolQueue()
    navigator = FakeNavigator(result=make_nav_result(success=True, total_waypoints=1))
    navigator.release_event = asyncio.Event()
    event_bus = FakeEventBus()
    dispatcher = build_dispatcher(dispatcher_module, patrol_queue=queue, navigator=navigator, event_bus=event_bus)

    async def run_dispatch() -> bool:
        return await dispatcher.dispatch_once()

    queue.next_cycle = FakeCycle(cycle_id="cycle-1")
    task = asyncio.create_task(run_dispatch())
    await navigator.started_event.wait()
    await dispatcher._handle_estop_triggered(SimpleNamespace(payload={"reason": "pressed"}))
    processed = await asyncio.wait_for(task, timeout=1.0)

    assert processed is True
    assert dispatcher.is_suspended() is True
    assert navigator.cancel_calls == ["pressed"]
    assert queue.failed_calls == [("cycle-1", "pressed")]


@pytest.mark.asyncio
async def test_start_and_stop_are_idempotent(dispatcher_module) -> None:
    event_bus = FakeEventBus()
    dispatcher = build_dispatcher(dispatcher_module, event_bus=event_bus)

    await dispatcher.start()
    await dispatcher.start()

    assert dispatcher.is_running() is True
    assert event_bus.subscriber_count(dispatcher_module.EventName.PATROL_SUSPENDED) == 1
    assert event_bus.subscriber_count(dispatcher_module.EventName.PATROL_RESUMED) == 1
    assert event_bus.subscriber_count(dispatcher_module.EventName.ESTOP_TRIGGERED) == 1
    assert event_bus.subscriber_count(dispatcher_module.EventName.QUADRUPED_ARRIVED_AT_WAYPOINT) == 1
    assert event_bus.subscriber_count(dispatcher_module.EventName.QUADRUPED_IDLE) == 1

    await dispatcher.stop()
    await dispatcher.stop()

    assert dispatcher.is_running() is False
    assert event_bus.subscriber_count(dispatcher_module.EventName.PATROL_SUSPENDED) == 0
    assert event_bus.subscriber_count(dispatcher_module.EventName.PATROL_RESUMED) == 0
    assert event_bus.subscriber_count(dispatcher_module.EventName.ESTOP_TRIGGERED) == 0
    assert event_bus.subscriber_count(dispatcher_module.EventName.QUADRUPED_ARRIVED_AT_WAYPOINT) == 0
    assert event_bus.subscriber_count(dispatcher_module.EventName.QUADRUPED_IDLE) == 0


@pytest.mark.asyncio
async def test_active_state_cleared_after_success(dispatcher_module) -> None:
    queue = FakePatrolQueue()
    queue.next_cycle = FakeCycle(cycle_id="cycle-1")
    dispatcher = build_dispatcher(dispatcher_module, patrol_queue=queue)

    assert await dispatcher.dispatch_once() is True

    state = await dispatcher.get_state()
    assert state.active_cycle_id is None
    assert state.active_route_id is None
    assert dispatcher.active_cycle_id() is None


@pytest.mark.asyncio
async def test_two_patrol_robots_can_run_two_independent_cycles(dispatcher_module) -> None:
    queue = FakePatrolQueue()
    queue.next_cycles = [
        FakeCycle(cycle_id="cycle-1", route_id="PATROL_ALPHA", robot_id="patrol_01"),
        FakeCycle(cycle_id="cycle-2", route_id="PATROL_BRAVO", robot_id="patrol_02"),
    ]
    patrol_01_nav = FakeNavigator()
    patrol_01_nav.release_event = asyncio.Event()
    patrol_01_nav.route_results["PATROL_ALPHA"] = make_nav_result(success=True, total_waypoints=2)
    patrol_02_nav = FakeNavigator()
    patrol_02_nav.release_event = asyncio.Event()
    patrol_02_nav.route_results["PATROL_BRAVO"] = make_nav_result(success=True, total_waypoints=2)
    event_bus = FakeEventBus()
    registry = FakeRobotRegistry(
        make_platform("patrol_01", navigator=patrol_01_nav),
        make_platform("patrol_02", navigator=patrol_02_nav),
    )
    dispatcher = build_dispatcher(
        dispatcher_module,
        patrol_queue=queue,
        navigator=FakeNavigator(),
        event_bus=event_bus,
        robot_registry=registry,
    )

    await dispatcher.start()
    try:
        await event_bus.publish(dispatcher_module.EventName.QUADRUPED_IDLE, {"robot_id": "patrol_01"})
        await event_bus.publish(dispatcher_module.EventName.QUADRUPED_IDLE, {"robot_id": "patrol_02"})
        await patrol_01_nav.started_event.wait()
        await patrol_02_nav.started_event.wait()
        await wait_for_condition(lambda: dispatcher._active_cycles == {"patrol_01": "cycle-1", "patrol_02": "cycle-2"})
        patrol_01_nav.release_event.set()
        patrol_02_nav.release_event.set()
        await wait_for_condition(lambda: len(queue.completed_calls) == 2)
    finally:
        patrol_01_nav.release_event.set()
        patrol_02_nav.release_event.set()
        await dispatcher.stop()

    assert patrol_01_nav.calls[0] == ("PATROL_ALPHA", "cycle-1")
    assert patrol_02_nav.calls[0] == ("PATROL_BRAVO", "cycle-2")
    assert queue.active_calls[:2] == ["cycle-1", "cycle-2"]
    assert queue.completed_calls[:2] == [
        ("cycle-1", {"waypoints_total": 2, "waypoints_observed": 0, "anomaly_ids": []}),
        ("cycle-2", {"waypoints_total": 2, "waypoints_observed": 0, "anomaly_ids": []}),
    ]


@pytest.mark.asyncio
async def test_robot_specific_route_affinity_is_respected(dispatcher_module) -> None:
    queue = FakePatrolQueue()
    queue.next_cycles = [
        FakeCycle(cycle_id="cycle-a", route_id="ROUTE_A", robot_id="patrol_01"),
        FakeCycle(cycle_id="cycle-b", route_id="ROUTE_B", robot_id="patrol_02"),
        FakeCycle(cycle_id="cycle-c", route_id="ROUTE_C"),
    ]
    patrol_01_nav = FakeNavigator()
    patrol_01_nav.route_results["ROUTE_A"] = make_nav_result(success=True, total_waypoints=1)
    patrol_01_nav.route_results["ROUTE_C"] = make_nav_result(success=True, total_waypoints=1)
    patrol_02_nav = FakeNavigator()
    patrol_02_nav.route_results["ROUTE_B"] = make_nav_result(success=True, total_waypoints=1)
    patrol_02_nav.route_results["ROUTE_C"] = make_nav_result(success=True, total_waypoints=1)
    registry = FakeRobotRegistry(
        make_platform("patrol_01", navigator=patrol_01_nav),
        make_platform("patrol_02", navigator=patrol_02_nav),
    )
    dispatcher = build_dispatcher(dispatcher_module, patrol_queue=queue, robot_registry=registry)

    assert await dispatcher._dispatch_for_robot("patrol_02") is True
    assert patrol_02_nav.calls[0] == ("ROUTE_B", "cycle-b")

    assert await dispatcher._dispatch_for_robot("patrol_01") is True
    assert patrol_01_nav.calls[0] == ("ROUTE_A", "cycle-a")

    queue.next_cycles = [FakeCycle(cycle_id="cycle-d", route_id="ROUTE_D")]
    patrol_01_nav.route_results["ROUTE_D"] = make_nav_result(success=True, total_waypoints=1)
    assert await dispatcher._dispatch_for_robot("patrol_01") is True
    assert patrol_01_nav.calls[-2] == ("ROUTE_D", "cycle-d")


@pytest.mark.asyncio
async def test_completion_for_patrol_01_does_not_affect_patrol_02(dispatcher_module) -> None:
    queue = FakePatrolQueue()
    queue.next_cycles = [
        FakeCycle(cycle_id="cycle-1", route_id="PATROL_ALPHA", robot_id="patrol_01"),
        FakeCycle(cycle_id="cycle-2", route_id="PATROL_BRAVO", robot_id="patrol_02"),
    ]
    patrol_01_nav = FakeNavigator()
    patrol_01_nav.release_event = asyncio.Event()
    patrol_01_nav.route_results["PATROL_ALPHA"] = make_nav_result(success=True, total_waypoints=1)
    patrol_02_nav = FakeNavigator()
    patrol_02_nav.release_event = asyncio.Event()
    patrol_02_nav.route_results["PATROL_BRAVO"] = make_nav_result(success=True, total_waypoints=1)
    event_bus = FakeEventBus()
    registry = FakeRobotRegistry(
        make_platform("patrol_01", navigator=patrol_01_nav),
        make_platform("patrol_02", navigator=patrol_02_nav),
    )
    dispatcher = build_dispatcher(dispatcher_module, patrol_queue=queue, event_bus=event_bus, robot_registry=registry)

    await dispatcher.start()
    try:
        await event_bus.publish(dispatcher_module.EventName.QUADRUPED_IDLE, {"robot_id": "patrol_01"})
        await event_bus.publish(dispatcher_module.EventName.QUADRUPED_IDLE, {"robot_id": "patrol_02"})
        await patrol_01_nav.started_event.wait()
        await patrol_02_nav.started_event.wait()
        await wait_for_condition(lambda: dispatcher._active_cycles == {"patrol_01": "cycle-1", "patrol_02": "cycle-2"})

        patrol_01_nav.release_event.set()
        await wait_for_condition(lambda: "patrol_01" not in dispatcher._active_cycles)
        assert dispatcher._active_cycles["patrol_02"] == "cycle-2"
    finally:
        patrol_02_nav.release_event.set()
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_failure_for_patrol_02_does_not_affect_patrol_01(dispatcher_module) -> None:
    queue = FakePatrolQueue()
    queue.next_cycles = [
        FakeCycle(cycle_id="cycle-1", route_id="PATROL_ALPHA", robot_id="patrol_01"),
        FakeCycle(cycle_id="cycle-2", route_id="PATROL_BRAVO", robot_id="patrol_02"),
    ]
    patrol_01_nav = FakeNavigator()
    patrol_01_nav.release_event = asyncio.Event()
    patrol_01_nav.route_results["PATROL_ALPHA"] = make_nav_result(success=True, total_waypoints=1)
    patrol_02_nav = FakeNavigator(result=make_nav_result(success=False, message="blocked"))
    patrol_02_nav.route_results["PATROL_BRAVO"] = make_nav_result(success=False, message="blocked")
    event_bus = FakeEventBus()
    registry = FakeRobotRegistry(
        make_platform("patrol_01", navigator=patrol_01_nav),
        make_platform("patrol_02", navigator=patrol_02_nav),
    )
    dispatcher = build_dispatcher(dispatcher_module, patrol_queue=queue, event_bus=event_bus, robot_registry=registry)

    await dispatcher.start()
    try:
        await event_bus.publish(dispatcher_module.EventName.QUADRUPED_IDLE, {"robot_id": "patrol_01"})
        await event_bus.publish(dispatcher_module.EventName.QUADRUPED_IDLE, {"robot_id": "patrol_02"})
        await patrol_01_nav.started_event.wait()
        await patrol_02_nav.started_event.wait()
        await wait_for_condition(lambda: dispatcher._active_cycles.get("patrol_01") == "cycle-1")
        await wait_for_condition(lambda: "patrol_02" not in dispatcher._active_cycles)
        assert dispatcher._active_cycles["patrol_01"] == "cycle-1"
        assert queue.failed_calls == [("cycle-2", "blocked")]
    finally:
        patrol_01_nav.release_event.set()
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_unknown_robot_id_event_is_ignored_safely(dispatcher_module) -> None:
    queue = FakePatrolQueue()
    queue.next_cycle = FakeCycle(cycle_id="cycle-1", robot_id="patrol_01")
    patrol_01_nav = FakeNavigator()
    patrol_01_nav.release_event = asyncio.Event()
    patrol_01_nav.route_results["PATROL_NORTH_LOOP"] = make_nav_result(success=True, total_waypoints=1)
    event_bus = FakeEventBus()
    registry = FakeRobotRegistry(make_platform("patrol_01", navigator=patrol_01_nav))
    dispatcher = build_dispatcher(dispatcher_module, patrol_queue=queue, event_bus=event_bus, robot_registry=registry)

    await dispatcher.start()
    try:
        await event_bus.publish(dispatcher_module.EventName.QUADRUPED_IDLE, {"robot_id": "patrol_01"})
        await patrol_01_nav.started_event.wait()
        await wait_for_condition(lambda: dispatcher._active_cycles.get("patrol_01") == "cycle-1")

        await dispatcher._handle_estop_triggered(SimpleNamespace(payload={"reason": "pressed", "robot_id": "patrol_999"}))

        assert dispatcher._active_cycles["patrol_01"] == "cycle-1"
        assert patrol_01_nav.cancel_calls == []
    finally:
        patrol_01_nav.release_event.set()
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_legacy_no_robot_id_idle_event_still_works(dispatcher_module) -> None:
    queue = FakePatrolQueue()
    queue.next_cycle = FakeCycle(cycle_id="cycle-1")
    navigator = FakeNavigator()
    navigator.release_event = asyncio.Event()
    event_bus = FakeEventBus()
    dispatcher = build_dispatcher(dispatcher_module, patrol_queue=queue, navigator=navigator, event_bus=event_bus)

    await dispatcher.start()
    try:
        await event_bus.publish(dispatcher_module.EventName.QUADRUPED_IDLE, {})
        await navigator.started_event.wait()
        await wait_for_condition(lambda: dispatcher._active_cycles.get("default") == "cycle-1")
    finally:
        navigator.release_event.set()
        await dispatcher.stop()

    assert navigator.calls[0] == ("PATROL_NORTH_LOOP", "cycle-1")


@pytest.mark.asyncio
async def test_dispatcher_uses_per_robot_navigator(dispatcher_module) -> None:
    queue = FakePatrolQueue()
    queue.next_cycle = FakeCycle(cycle_id="cycle-2", route_id="PATROL_BRAVO", robot_id="patrol_02")
    legacy_navigator = FakeNavigator()
    patrol_02_nav = FakeNavigator()
    patrol_02_nav.release_event = asyncio.Event()
    patrol_02_nav.route_results["PATROL_BRAVO"] = make_nav_result(success=True, total_waypoints=1)
    event_bus = FakeEventBus()
    registry = FakeRobotRegistry(make_platform("patrol_02", navigator=patrol_02_nav))
    dispatcher = build_dispatcher(
        dispatcher_module,
        patrol_queue=queue,
        navigator=legacy_navigator,
        event_bus=event_bus,
        robot_registry=registry,
    )

    await dispatcher.start()
    try:
        await event_bus.publish(dispatcher_module.EventName.QUADRUPED_IDLE, {"robot_id": "patrol_02"})
        await patrol_02_nav.started_event.wait()
    finally:
        patrol_02_nav.release_event.set()
        await dispatcher.stop()

    assert patrol_02_nav.calls[0] == ("PATROL_BRAVO", "cycle-2")
    assert legacy_navigator.calls == []


@pytest.mark.asyncio
async def test_logistics_robot_is_not_selected_for_patrol(dispatcher_module) -> None:
    queue = FakePatrolQueue()
    queue.next_cycle = FakeCycle(cycle_id="cycle-1", robot_id="patrol_01")
    logistics_nav = FakeNavigator()
    patrol_nav = FakeNavigator()
    patrol_nav.release_event = asyncio.Event()
    event_bus = FakeEventBus()
    registry = FakeRobotRegistry(
        make_platform("logistics_01", navigator=logistics_nav, role="logistics"),
        make_platform("patrol_01", navigator=patrol_nav, role="patrol"),
    )
    dispatcher = build_dispatcher(dispatcher_module, patrol_queue=queue, event_bus=event_bus, robot_registry=registry)

    await dispatcher.start()
    try:
        await event_bus.publish(dispatcher_module.EventName.QUADRUPED_IDLE, {})
        await patrol_nav.started_event.wait()
    finally:
        patrol_nav.release_event.set()
        await dispatcher.stop()

    assert patrol_nav.calls[0] == ("PATROL_NORTH_LOOP", "cycle-1")
    assert logistics_nav.calls == []


def test_global_get_patrol_dispatcher_returns_dispatcher(dispatcher_module) -> None:
    assert dispatcher_module.get_patrol_dispatcher() is dispatcher_module.patrol_dispatcher
    assert isinstance(dispatcher_module.patrol_dispatcher, dispatcher_module.PatrolDispatcher)
