from __future__ import annotations

import math
from types import SimpleNamespace

import pytest


class FakeReporter:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.events: list[dict] = []

    def report(self, **kwargs):
        if self.fail:
            raise RuntimeError("diagnostic failed")
        self.events.append(kwargs)
        return SimpleNamespace(**kwargs)


class FakeEventBus:
    def __init__(self):
        self.events = []

    def publish_nowait(self, event_name, payload=None, **kwargs):
        self.events.append((event_name, payload or {}, kwargs))


class FakeSDK:
    def __init__(self):
        self.connected = True
        self.fail_move = False

    def initRobot(self, *_args):
        return self.connected

    def passive(self):
        return True

    def standUp(self):
        return True

    def move(self, *_args):
        return not self.fail_move

    def checkConnect(self):
        return self.connected


@pytest.mark.asyncio
async def test_sdk_adapter_emits_connect_and_command_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    import shared.quadruped.sdk_adapter as sdk_module

    monkeypatch.setattr(sdk_module, "get_event_bus", lambda: FakeEventBus())
    reporter = FakeReporter()
    fake_sdk = FakeSDK()
    fake_sdk.connected = False
    adapter = sdk_module.SDKAdapter(sdk_client=fake_sdk, reporter=reporter)

    assert await adapter.connect() is False
    assert reporter.events[-1]["event"] == "sdk.connect_failed"
    assert reporter.events[-1]["error_code"] == "sdk.connect_failed"

    fake_sdk.connected = True
    await adapter.connect()
    await adapter.stand_up()
    fake_sdk.fail_move = True

    assert await adapter.move(0.1, 0.0, 0.0) is False
    assert any(event["event"] == "sdk.command_failed" for event in reporter.events)


def test_sdk_import_fallback_emits_only_with_injected_reporter(monkeypatch: pytest.MonkeyPatch) -> None:
    import shared.quadruped.sdk_adapter as sdk_module

    monkeypatch.setattr(sdk_module.importlib, "import_module", lambda _name: (_ for _ in ()).throw(ImportError("missing")))
    reporter = FakeReporter()

    adapter = sdk_module.SDKAdapter(sdk_client=None, allow_mock=True, reporter=reporter)

    assert adapter._sdk_client.__class__.__name__ == "_NullSDKClient"
    assert reporter.events[-1]["event"] == "sdk.import_failed"


@pytest.mark.asyncio
async def test_sdk_diagnostic_failure_does_not_break_adapter() -> None:
    from shared.quadruped.sdk_adapter import SDKAdapter

    fake_sdk = FakeSDK()
    fake_sdk.connected = False
    adapter = SDKAdapter(sdk_client=fake_sdk, reporter=FakeReporter(fail=True))

    assert await adapter.connect() is False


class FakeHeartbeatAdapter:
    def __init__(self):
        self.fail_next_move = False

    async def move(self, *_args):
        if self.fail_next_move:
            self.fail_next_move = False
            return False
        return True

    async def stop_motion(self):
        return True


@pytest.mark.asyncio
async def test_heartbeat_emits_failure_and_recovery_diagnostics() -> None:
    from shared.quadruped.heartbeat import HeartbeatController

    reporter = FakeReporter()
    adapter = FakeHeartbeatAdapter()
    controller = HeartbeatController(sdk_adapter=adapter, interval_seconds=0.01, reporter=reporter)
    adapter.fail_next_move = True

    await controller._send_once()
    await controller._send_once()

    assert [event["event"] for event in reporter.events] == [
        "heartbeat.command_failed",
        "heartbeat.recovered",
    ]


@pytest.mark.asyncio
async def test_heartbeat_diagnostic_failure_does_not_break_send_once() -> None:
    from shared.quadruped.heartbeat import HeartbeatController

    adapter = FakeHeartbeatAdapter()
    adapter.fail_next_move = True
    controller = HeartbeatController(sdk_adapter=adapter, interval_seconds=0.01, reporter=FakeReporter(fail=True))

    await controller._send_once()

    assert controller.last_send_ok() is False


class FakeTelemetrySDK:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def get_telemetry_snapshot(self):
        return self.snapshot


class FakeDatabase:
    async def initialize(self):
        return None

    async def log_telemetry(self, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_state_monitor_emits_connection_and_battery_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.quadruped.sdk_adapter import QuadrupedMode, QuadrupedTelemetrySnapshot
    import shared.quadruped.state_monitor as state_monitor_module

    monkeypatch.setattr(state_monitor_module, "get_event_bus", lambda: FakeEventBus())
    reporter = FakeReporter()
    sdk = FakeTelemetrySDK(
        QuadrupedTelemetrySnapshot(
            battery_pct=20,
            position=(1.0, 2.0, 0.0),
            rpy=(0.0, 0.0, 0.0),
            control_mode=0,
            connection_ok=False,
            mode=QuadrupedMode.ERROR,
        )
    )
    monitor = state_monitor_module.StateMonitor(
        sdk_adapter=sdk,
        database=FakeDatabase(),
        persist_telemetry=False,
        reporter=reporter,
    )
    monitor._previous_connection_ok = True

    await monitor.poll_once()
    await monitor.poll_once()

    names = [event["event"] for event in reporter.events]
    assert names.count("sdk.connection_lost") == 1
    assert names.count("battery.low") == 1
    assert names.count("battery.critical") == 1


@pytest.mark.asyncio
async def test_state_monitor_legacy_ids_are_not_used_for_route_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.quadruped.sdk_adapter import QuadrupedMode, QuadrupedTelemetrySnapshot
    import shared.quadruped.state_monitor as state_monitor_module

    monkeypatch.setattr(state_monitor_module, "get_event_bus", lambda: FakeEventBus())
    reporter = FakeReporter()
    sdk = FakeTelemetrySDK(
        QuadrupedTelemetrySnapshot(
            battery_pct=100,
            position=("bad", 0.0, 0.0),
            rpy=(0.0, 0.0, 0.0),
            control_mode=0,
            connection_ok=True,
            mode=QuadrupedMode.STANDING,
        )
    )
    monitor = state_monitor_module.StateMonitor(
        sdk_adapter=sdk,
        database=FakeDatabase(),
        persist_telemetry=False,
        reporter=reporter,
    )

    await monitor.poll_once()

    assert reporter.events[-1]["event"] == "telemetry.invalid"
    assert reporter.events[-1]["error_code"] == "sdk.position_invalid"


def test_obstacle_detector_transition_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.navigation.obstacle import ObstacleDetector, ObstacleStatus
    import shared.navigation.obstacle as obstacle_module

    monkeypatch.setattr(obstacle_module, "get_event_bus", lambda: FakeEventBus())
    reporter = FakeReporter()
    detector = ObstacleDetector(reporter=reporter)

    detector._publish_transition(ObstacleStatus.clear(), ObstacleStatus.detected(source="test", confidence=1.0))
    detector._publish_transition(ObstacleStatus.detected(source="test", confidence=1.0), ObstacleStatus.clear())

    assert [event["event"] for event in reporter.events] == ["obstacle.detected", "obstacle.cleared"]
    assert reporter.events[0]["error_code"] == "obstacle.detected"
    assert reporter.events[1]["error_code"] == "obstacle.cleared"


def test_obstacle_detector_diagnostic_failure_does_not_break_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.navigation.obstacle import ObstacleDetector, ObstacleStatus
    import shared.navigation.obstacle as obstacle_module

    monkeypatch.setattr(obstacle_module, "get_event_bus", lambda: FakeEventBus())
    detector = ObstacleDetector(reporter=FakeReporter(fail=True))

    detector._publish_transition(ObstacleStatus.clear(), ObstacleStatus.detected(source="test", confidence=1.0))


class FakeRouteStore:
    def __init__(self, route):
        self.route = route

    async def get_route(self, _origin_id, _destination_id):
        return self.route.waypoints

    async def get_route_definition(self, _route_id):
        return self.route

    async def list_routes(self, active=None):
        return [self.route]


class FakeNavigatorStateMonitor:
    def __init__(self, state):
        self.state = state

    async def get_current_state(self):
        return self.state

    async def poll_once(self):
        return self.state


class FakeNavigatorHeartbeat:
    async def set_target_velocity(self, *_args, **_kwargs):
        return None

    async def clear_target_velocity(self, **_kwargs):
        return None


def _route(target=(0.0, 0.0)):
    from shared.navigation.route_store import RouteDefinition, Waypoint

    return RouteDefinition(
        id="route-1",
        name="route-1",
        origin_id="origin",
        destination_id="destination",
        waypoints=[
            Waypoint(name="wp-1", x=target[0], y=target[1], heading_deg=0.0, velocity=0.25),
        ],
    )


def _state(*, connection_ok=True):
    from shared.quadruped.sdk_adapter import QuadrupedMode
    from shared.quadruped.state_monitor import QuadrupedState
    from datetime import datetime, timezone

    return QuadrupedState(
        timestamp=datetime.now(timezone.utc),
        battery_pct=100,
        position=(0.0, 0.0, 0.0),
        rpy=(0.0, 0.0, 0.0),
        control_mode=0,
        connection_ok=connection_ok,
        mode=QuadrupedMode.STANDING,
    )


@pytest.mark.asyncio
async def test_navigator_emits_started_completed_and_failed_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.core.event_bus import EventBus
    from shared.navigation.navigator import Navigator
    import shared.navigation.navigator as navigator_module

    event_bus = EventBus()
    await event_bus.start()
    monkeypatch.setattr(navigator_module, "get_event_bus", lambda: event_bus)
    reporter = FakeReporter()
    navigator = Navigator(
        route_store=FakeRouteStore(_route()),
        state_monitor=FakeNavigatorStateMonitor(_state()),
        heartbeat=FakeNavigatorHeartbeat(),
        waypoint_tolerance_m=0.1,
        reporter=reporter,
    )
    try:
        result = await navigator.execute_route("origin", "destination", task_id="task-1")
    finally:
        await event_bus.stop()

    assert result.success is True
    assert [event["event"] for event in reporter.events[:2]] == ["navigation.started", "navigation.completed"]
    assert reporter.events[0]["context"] == {"route_id": "route-1", "task_id": "task-1"}

    event_bus = EventBus()
    await event_bus.start()
    monkeypatch.setattr(navigator_module, "get_event_bus", lambda: event_bus)
    failing_reporter = FakeReporter()
    failing_navigator = Navigator(
        route_store=FakeRouteStore(_route(target=(1.0, 0.0))),
        state_monitor=FakeNavigatorStateMonitor(_state(connection_ok=False)),
        heartbeat=FakeNavigatorHeartbeat(),
        waypoint_tolerance_m=0.1,
        reporter=failing_reporter,
    )
    try:
        failed = await failing_navigator.execute_route("origin", "destination", task_id="task-2")
    finally:
        await event_bus.stop()

    assert failed.success is False
    assert failing_reporter.events[-1]["event"] == "navigation.failed"
    assert failing_reporter.events[-1]["context"] == {"route_id": "route-1", "task_id": "task-2"}


@pytest.mark.asyncio
async def test_navigator_diagnostic_failure_does_not_break_route(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.core.event_bus import EventBus
    from shared.navigation.navigator import Navigator
    import shared.navigation.navigator as navigator_module

    event_bus = EventBus()
    await event_bus.start()
    monkeypatch.setattr(navigator_module, "get_event_bus", lambda: event_bus)
    navigator = Navigator(
        route_store=FakeRouteStore(_route()),
        state_monitor=FakeNavigatorStateMonitor(_state()),
        heartbeat=FakeNavigatorHeartbeat(),
        waypoint_tolerance_m=0.1,
        reporter=FakeReporter(fail=True),
    )
    try:
        result = await navigator.execute_route("origin", "destination")
    finally:
        await event_bus.stop()

    assert result.success is True


def test_shared_critical_instrumentation_has_no_app_imports_or_workflow_terms() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    files = [
        root / "shared/quadruped/sdk_adapter.py",
        root / "shared/quadruped/heartbeat.py",
        root / "shared/quadruped/state_monitor.py",
        root / "shared/navigation/obstacle.py",
        root / "shared/navigation/navigator.py",
    ]
    forbidden = (
        "from apps",
        "import apps",
        "LINE_A",
        "LINE_B",
        "LINE_C",
        "Sumitomo",
        "HUMAN_CONFIRMED_LOAD",
        "HUMAN_CONFIRMED_UNLOAD",
        "PATROL_",
        "patrol cycle",
        "patrol waypoint",
    )

    for path in files:
        content = path.read_text(encoding="utf-8")
        for term in forbidden:
            assert term not in content
