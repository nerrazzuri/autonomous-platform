from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TEST_OPERATOR_TOKEN = "test-operator-token"
TEST_QA_TOKEN = "test-qa-token"
TEST_SUPERVISOR_TOKEN = "test-supervisor-token"


def build_auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.close_code: int | None = None
        self.sent_messages: list[dict[str, object]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.close_code = code

    async def send_json(self, message: dict[str, object]) -> None:
        self.sent_messages.append(message)


class AsyncLifecycleStub:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    async def start(self) -> None:
        self.calls.append(f"start:{self.name}")

    async def stop(self) -> None:
        self.calls.append(f"stop:{self.name}")


class DatabaseStub:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def initialize(self) -> None:
        self.calls.append("database.initialize")

    async def close(self) -> None:
        self.calls.append("database.close")


class RouteStoreStub:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def load(self) -> None:
        self.calls.append("route_store.load")


class EventBusLifecycleStub:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def start(self) -> None:
        self.calls.append("event_bus.start")

    async def stop(self) -> None:
        self.calls.append("event_bus.stop")


class SDKStub:
    def __init__(self, *, robot_ip: str, local_ip: str, sdk_port: int, sdk_lib_path: str | None) -> None:
        self.robot_ip = robot_ip
        self.local_ip = local_ip
        self.sdk_port = sdk_port
        self.sdk_lib_path = sdk_lib_path
        self.connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.passive_calls = 0
        self.stand_up_calls = 0

    async def connect(self) -> bool:
        self.connect_calls += 1
        self.connected = True
        return True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False

    async def passive(self) -> bool:
        self.passive_calls += 1
        return True

    async def stand_up(self) -> bool:
        self.stand_up_calls += 1
        return True


class SDKFactory:
    def __init__(self) -> None:
        self.instances: list[SDKStub] = []

    def __call__(self, *, quadruped_ip: str, local_ip: str, sdk_port: int, sdk_lib_path: str | None = None) -> SDKStub:
        instance = SDKStub(
            robot_ip=quadruped_ip,
            local_ip=local_ip,
            sdk_port=sdk_port,
            sdk_lib_path=sdk_lib_path,
        )
        self.instances.append(instance)
        return instance


class HeartbeatStub:
    def __init__(self, *, robot_id: str, calls: list[str], sdk_adapter: SDKStub) -> None:
        self.robot_id = robot_id
        self.calls = calls
        self.sdk_adapter = sdk_adapter

    async def start(self) -> None:
        self.calls.append(f"start:heartbeat:{self.robot_id}")

    async def stop(self) -> None:
        self.calls.append(f"stop:heartbeat:{self.robot_id}")


class HeartbeatFactory:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.instances: list[HeartbeatStub] = []

    def __call__(self, *, sdk_adapter: SDKStub, robot_id: str) -> HeartbeatStub:
        instance = HeartbeatStub(robot_id=robot_id, calls=self.calls, sdk_adapter=sdk_adapter)
        self.instances.append(instance)
        return instance


class RobotState:
    def __init__(self, *, battery_pct: int, position: tuple[float, float, float], control_mode: int = 18) -> None:
        self.battery_pct = battery_pct
        self.position = position
        self.connection_ok = True
        self.control_mode = control_mode
        self.mode = SimpleNamespace(value="standing")
        self.timestamp = SimpleNamespace(isoformat=lambda: "2026-04-28T00:00:00+00:00")


class StateMonitorStub:
    def __init__(self, *, robot_id: str, calls: list[str], sdk_adapter: SDKStub, database: DatabaseStub) -> None:
        self.robot_id = robot_id
        self.calls = calls
        self.sdk_adapter = sdk_adapter
        self.database = database
        offset = sum(ord(char) for char in robot_id) % 10
        self.state = RobotState(
            battery_pct=70 + offset,
            position=(1.0 + offset, 2.0 + offset, 0.0),
        )

    async def start(self) -> None:
        self.calls.append(f"start:state_monitor:{self.robot_id}")

    async def stop(self) -> None:
        self.calls.append(f"stop:state_monitor:{self.robot_id}")

    async def get_current_state(self):
        return self.state

    async def poll_once(self):
        return self.state


class StateMonitorFactory:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.instances: list[StateMonitorStub] = []

    def __call__(self, *, sdk_adapter: SDKStub, database: DatabaseStub, robot_id: str) -> StateMonitorStub:
        instance = StateMonitorStub(robot_id=robot_id, calls=self.calls, sdk_adapter=sdk_adapter, database=database)
        self.instances.append(instance)
        return instance


class NavigatorStub:
    def __init__(
        self,
        *,
        robot_id: str,
        sdk_adapter: SDKStub,
        route_store: RouteStoreStub,
        state_monitor: StateMonitorStub,
        heartbeat: HeartbeatStub,
    ) -> None:
        self.robot_id = robot_id
        self.sdk_adapter = sdk_adapter
        self.route_store = route_store
        self.state_monitor = state_monitor
        self.heartbeat = heartbeat
        self.route_calls: list[dict[str, object]] = []
        self.route_id_calls: list[dict[str, object]] = []
        self.cancel_calls: list[str] = []
        self._navigating = False

    def is_navigating(self) -> bool:
        return self._navigating

    async def execute_route(self, origin_id: str, destination_id: str, *, task_id: str | None = None):
        from shared.navigation.navigator import NavigationResult

        self._navigating = True
        self.route_calls.append(
            {
                "origin_id": origin_id,
                "destination_id": destination_id,
                "task_id": task_id,
            }
        )
        self._navigating = False
        return NavigationResult(
            success=True,
            route_id=f"{origin_id}_TO_{destination_id}",
            origin_id=origin_id,
            destination_id=destination_id,
            completed_waypoints=2,
            total_waypoints=2,
            message="completed",
        )

    async def execute_route_by_id(self, route_id: str, *, task_id: str | None = None):
        from shared.navigation.navigator import NavigationResult

        self._navigating = True
        self.route_id_calls.append({"route_id": route_id, "task_id": task_id})
        self._navigating = False
        return NavigationResult(
            success=True,
            route_id=route_id,
            origin_id="dock",
            destination_id="dock",
            completed_waypoints=1,
            total_waypoints=1,
            message="completed",
        )

    async def cancel_navigation(self, reason: str = "cancelled") -> None:
        self.cancel_calls.append(reason)
        self._navigating = False


class NavigatorFactory:
    def __init__(self) -> None:
        self.instances: list[NavigatorStub] = []

    def __call__(
        self,
        *,
        sdk_adapter: SDKStub,
        robot_id: str,
        route_store: RouteStoreStub,
        state_monitor: StateMonitorStub,
        heartbeat: HeartbeatStub,
    ) -> NavigatorStub:
        instance = NavigatorStub(
            robot_id=robot_id,
            sdk_adapter=sdk_adapter,
            route_store=route_store,
            state_monitor=state_monitor,
            heartbeat=heartbeat,
        )
        self.instances.append(instance)
        return instance


class FakeTaskQueue:
    def __init__(self, tasks: list[SimpleNamespace]) -> None:
        self.tasks = list(tasks)
        self.dispatched_ids: list[str] = []
        self.completed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    async def get_next_task(self, robot_position=None):
        if not self.tasks:
            return None
        return self.tasks.pop(0)

    async def mark_dispatched(self, task_id: str) -> None:
        self.dispatched_ids.append(task_id)

    async def mark_completed(self, task_id: str) -> None:
        self.completed.append(task_id)

    async def mark_failed(self, task_id: str, notes: str | None = None) -> None:
        self.failed.append((task_id, notes or ""))

    async def get_task(self, task_id: str):
        for task in self.tasks:
            if task.id == task_id:
                return task
        return SimpleNamespace(id=task_id, station_id="A", destination_id="QA", status="queued")


class FakePatrolQueue:
    def __init__(self, cycles: list[SimpleNamespace]) -> None:
        self.cycles = list(cycles)
        self.active: list[str] = []
        self.completed: list[str] = []

    async def get_next_cycle(self, robot_id: str | None = None):
        for index, cycle in enumerate(self.cycles):
            cycle_robot_id = getattr(cycle, "robot_id", None)
            if cycle_robot_id is None or cycle_robot_id == robot_id:
                return self.cycles.pop(index)
        return None

    async def mark_active(self, cycle_id: str) -> None:
        self.active.append(cycle_id)

    async def mark_completed(self, cycle_id: str, stats_dict=None) -> None:
        self.completed.append(cycle_id)

    async def mark_failed(self, cycle_id: str, reason: str) -> None:
        raise AssertionError(f"unexpected mark_failed for {cycle_id}: {reason}")


class FakeDispatcherState:
    def __init__(self, active_tasks: dict[str, str]) -> None:
        self._active_tasks = dict(active_tasks)

    async def get_state(self):
        return SimpleNamespace(active_task_id=None)


class FakePatrolDispatcherState:
    def __init__(self, active_cycles: dict[str, str], active_routes: dict[str, str]) -> None:
        self._active_cycles = dict(active_cycles)
        self._active_routes = dict(active_routes)


class FakeAsyncService:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


@pytest.fixture(autouse=True)
def registry_and_singleton_isolation():
    import shared.navigation.navigator as navigator_module
    import shared.quadruped.heartbeat as heartbeat_module
    import shared.quadruped.robot_registry as robot_registry_module
    import shared.quadruped.sdk_adapter as sdk_adapter_module
    import shared.quadruped.state_monitor as state_monitor_module

    registry = robot_registry_module.get_robot_registry()
    registry_snapshot = list(registry.all())
    singleton_snapshot = {
        "sdk_adapter": sdk_adapter_module.sdk_adapter,
        "heartbeat_controller": heartbeat_module.heartbeat_controller,
        "state_monitor": state_monitor_module.state_monitor,
        "navigator": navigator_module.navigator,
    }

    yield

    registry.clear()
    for platform in registry_snapshot:
        registry.register(platform)
    sdk_adapter_module.sdk_adapter = singleton_snapshot["sdk_adapter"]
    heartbeat_module.heartbeat_controller = singleton_snapshot["heartbeat_controller"]
    state_monitor_module.state_monitor = singleton_snapshot["state_monitor"]
    navigator_module.navigator = singleton_snapshot["navigator"]


def _write_provisioned_robot(
    robots_yaml_path: Path,
    *,
    robot_id: str,
    dog_mac: str,
    dog_ip: str,
    role: str,
    display_name: str,
) -> None:
    from shared.provisioning.provision_backend import write_robot_entry
    from shared.provisioning.provision_models import ProvisionResult

    write_robot_entry(
        ProvisionResult(
            success=True,
            robot_id=robot_id,
            dog_mac=dog_mac,
            dog_ip=dog_ip,
            pc_ip="192.168.1.10",
            role=role,
            message="Provisioning complete",
        ),
        role,
        robots_yaml_path,
        display_name=display_name,
    )


def _poll_job_status(client: TestClient, job_id: str, token: str, *, timeout_seconds: float = 2.0) -> dict[str, object]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = client.get(f"/provision/status/{job_id}", headers=build_auth_header(token))
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"succeeded", "failed"}:
            return body
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for provisioning job {job_id}")


@pytest.mark.asyncio
async def test_multi_robot_stack_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from shared.core.config import AppConfig, AuthSection
    from shared.core.event_bus import Event, EventBus, EventName
    from shared.core.robot_config import RobotConfigLoader
    from shared.quadruped.robot_registry import get_robot_registry
    from shared.runtime import base_startup as startup_module
    from shared.api import ws_broker as ws_module
    from apps.logistics.tasks.dispatcher import Dispatcher
    from apps.patrol.tasks.patrol_dispatcher import PatrolDispatcher
    from apps.logistics.api import rest as logistics_rest
    from apps.patrol.api import rest as patrol_rest
    import shared.api.auth as auth_module

    robots_yaml_path = tmp_path / "robots.yaml"
    _write_provisioned_robot(
        robots_yaml_path,
        robot_id="logistics_01",
        dog_mac="aa:bb:cc:dd:ee:01",
        dog_ip="192.168.1.51",
        role="logistics",
        display_name="Logistics Robot 1",
    )
    _write_provisioned_robot(
        robots_yaml_path,
        robot_id="logistics_02",
        dog_mac="aa:bb:cc:dd:ee:02",
        dog_ip="192.168.1.52",
        role="logistics",
        display_name="Logistics Robot 2",
    )
    _write_provisioned_robot(
        robots_yaml_path,
        robot_id="patrol_01",
        dog_mac="aa:bb:cc:dd:ee:03",
        dog_ip="192.168.1.53",
        role="patrol",
        display_name="Patrol Robot 1",
    )

    loaded_configs = RobotConfigLoader(robots_yaml_path).load()
    assert [config.connection.robot_id for config in loaded_configs] == ["logistics_01", "logistics_02", "patrol_01"]

    calls: list[str] = []
    config = AppConfig(
        auth=AuthSection(
            operator_token=TEST_OPERATOR_TOKEN,
            qa_token=TEST_QA_TOKEN,
            supervisor_token=TEST_SUPERVISOR_TOKEN,
        )
    )
    config.quadruped.auto_stand_on_startup = False
    config.quadruped.sdk_lib_path = "sdk/zsl-1"

    database = DatabaseStub(calls)
    route_store = RouteStoreStub(calls)
    event_bus = EventBusLifecycleStub(calls)
    obstacle_detector = AsyncLifecycleStub("obstacle_detector", calls)
    sdk_factory = SDKFactory()
    heartbeat_factory = HeartbeatFactory(calls)
    state_monitor_factory = StateMonitorFactory(calls)
    navigator_factory = NavigatorFactory()

    monkeypatch.setattr(startup_module, "setup_logging", lambda: None)
    monkeypatch.setattr(startup_module, "get_config", lambda: config)
    monkeypatch.setattr(startup_module, "_resolve_robot_config_path", lambda _config: robots_yaml_path)
    monkeypatch.setattr(startup_module, "get_database", lambda: database)
    monkeypatch.setattr(startup_module, "get_route_store", lambda: route_store)
    monkeypatch.setattr(startup_module, "get_event_bus", lambda: event_bus)
    monkeypatch.setattr(startup_module, "get_obstacle_detector", lambda: obstacle_detector)
    monkeypatch.setattr(startup_module, "SDKAdapter", sdk_factory)
    monkeypatch.setattr(startup_module, "HeartbeatController", heartbeat_factory)
    monkeypatch.setattr(startup_module, "StateMonitor", state_monitor_factory)
    monkeypatch.setattr(startup_module, "Navigator", navigator_factory)

    await startup_module.startup_system()

    registry = get_robot_registry()
    assert [platform.robot_id for platform in registry.all()] == ["logistics_01", "logistics_02", "patrol_01"]
    assert len({id(platform.sdk_adapter) for platform in registry.all()}) == 3
    assert len({id(platform.heartbeat) for platform in registry.all()}) == 3
    assert len({id(platform.state_monitor) for platform in registry.all()}) == 3
    assert len({id(platform.navigator) for platform in registry.all()}) == 3

    logistics_queue = FakeTaskQueue(
        [
            SimpleNamespace(id="task-log-1", station_id="A", destination_id="QA", status="queued"),
            SimpleNamespace(id="task-log-2", station_id="B", destination_id="Dock", status="queued"),
        ]
    )
    logistics_dispatcher = Dispatcher(task_queue=logistics_queue, robot_registry=registry)

    assert await logistics_dispatcher._dispatch_for_robot("logistics_01") is True
    assert await logistics_dispatcher._dispatch_for_robot("logistics_02") is True
    assert registry.get("logistics_01").navigator.route_calls[0]["task_id"] == "task-log-1"
    assert registry.get("logistics_02").navigator.route_calls[0]["task_id"] == "task-log-2"
    assert logistics_queue.dispatched_ids == ["task-log-1", "task-log-2"]

    patrol_queue = FakePatrolQueue(
        [
            SimpleNamespace(cycle_id="cycle-patrol-1", route_id="patrol-route-1", robot_id="patrol_01"),
        ]
    )
    patrol_dispatcher = PatrolDispatcher(
        patrol_queue=patrol_queue,
        robot_registry=registry,
        event_bus=SimpleNamespace(publish=lambda *args, **kwargs: asyncio.sleep(0)),
    )
    assert await patrol_dispatcher._dispatch_for_robot("patrol_01") is True
    assert registry.get("patrol_01").navigator.route_id_calls[0]["task_id"] == "cycle-patrol-1"
    assert await patrol_dispatcher._dispatch_for_robot("logistics_01") is False

    logistics_dispatch_state = FakeDispatcherState({"logistics_01": "task-log-1", "logistics_02": "task-log-2"})
    patrol_dispatch_state = FakePatrolDispatcherState({"patrol_01": "cycle-patrol-1"}, {"patrol_01": "patrol-route-1"})

    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    monkeypatch.setattr(logistics_rest, "get_config", lambda: config)
    monkeypatch.setattr(logistics_rest, "startup_system", lambda: asyncio.sleep(0))
    monkeypatch.setattr(logistics_rest, "shutdown_system", lambda: asyncio.sleep(0))
    monkeypatch.setattr(logistics_rest, "get_ws_broker", lambda: FakeAsyncService())
    monkeypatch.setattr(logistics_rest, "get_alert_manager", lambda: FakeAsyncService())
    monkeypatch.setattr(logistics_rest, "get_dispatcher_dep", lambda: logistics_dispatch_state)

    monkeypatch.setattr(patrol_rest, "get_config", lambda: config)
    monkeypatch.setattr(patrol_rest, "startup_system", lambda: asyncio.sleep(0))
    monkeypatch.setattr(patrol_rest, "shutdown_system", lambda: asyncio.sleep(0))
    monkeypatch.setattr(patrol_rest, "get_ws_broker", lambda: FakeAsyncService())
    monkeypatch.setattr(patrol_rest, "get_alert_manager", lambda: FakeAsyncService())
    monkeypatch.setattr(patrol_rest, "get_patrol_dispatcher_dep", lambda: patrol_dispatch_state)
    monkeypatch.setattr(patrol_rest, "get_patrol_scheduler_dep", lambda: SimpleNamespace(get_state=lambda: asyncio.sleep(0)))
    monkeypatch.setattr(patrol_rest, "get_patrol_watchdog_dep", lambda: SimpleNamespace(get_state=lambda: asyncio.sleep(0)))

    with TestClient(logistics_rest.create_app()) as logistics_client:
        response = logistics_client.get("/robots", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))
        assert response.status_code == 200
        logistics_robot_ids = [robot["robot_id"] for robot in response.json()]
        assert logistics_robot_ids == ["logistics_01", "logistics_02"]

    with TestClient(patrol_rest.create_app()) as patrol_client:
        response = patrol_client.get("/robots", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))
        assert response.status_code == 200
        assert [robot["robot_id"] for robot in response.json()] == ["patrol_01"]

    broker = ws_module.WebSocketBroker(event_bus=EventBus())
    ws_log_1 = FakeWebSocket()
    ws_log_2 = FakeWebSocket()
    ws_supervisor = FakeWebSocket()

    await broker.connect(ws_log_1, token=TEST_OPERATOR_TOKEN, robot_id="logistics_01")
    await broker.connect(ws_log_2, token=TEST_OPERATOR_TOKEN, robot_id="logistics_02")
    await broker.connect(ws_supervisor, token=TEST_SUPERVISOR_TOKEN)

    await broker.handle_event(Event(name=EventName.QUADRUPED_TELEMETRY, payload={"robot_id": "logistics_01", "battery_pct": 77}))
    await broker.handle_event(Event(name=EventName.QUADRUPED_TELEMETRY, payload={"robot_id": "logistics_02", "battery_pct": 66}))

    assert [message["payload"]["robot_id"] for message in ws_log_1.sent_messages] == ["logistics_01"]
    assert [message["payload"]["robot_id"] for message in ws_log_2.sent_messages] == ["logistics_02"]
    assert [message["payload"]["robot_id"] for message in ws_supervisor.sent_messages] == ["logistics_01", "logistics_02"]

    await startup_module.shutdown_system()
    assert registry.count() == 0


def test_provisioning_api_flow_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from shared.core.config import AppConfig, AuthSection
    from shared.provisioning.provision_models import ProvisionResult, WifiNetwork
    from apps.logistics.api import rest as rest_module
    import shared.api.auth as auth_module

    config = AppConfig(
        auth=AuthSection(
            operator_token=TEST_OPERATOR_TOKEN,
            qa_token=TEST_QA_TOKEN,
            supervisor_token=TEST_SUPERVISOR_TOKEN,
        )
    )
    robots_yaml_path = tmp_path / "robots.yaml"

    async def _noop_async() -> None:
        return None

    monkeypatch.setattr(auth_module, "get_config", lambda: config)
    monkeypatch.setattr(rest_module, "get_config", lambda: config)
    monkeypatch.setattr(rest_module, "startup_system", _noop_async)
    monkeypatch.setattr(rest_module, "shutdown_system", _noop_async)
    monkeypatch.setattr(rest_module, "get_ws_broker", lambda: FakeAsyncService())
    monkeypatch.setattr(rest_module, "get_alert_manager", lambda: FakeAsyncService())
    monkeypatch.setattr(rest_module, "_get_provisioning_robots_yaml_path", lambda: robots_yaml_path)
    monkeypatch.setattr(
        rest_module.provision_backend,
        "scan_wifi_networks",
        lambda: [
            WifiNetwork(ssid="D1-Ultra:aa:bb:cc:dd:ee", signal=88, security="WPA2", is_robot_ap=True),
            WifiNetwork(ssid="FACTORY_WIFI", signal=72, security="WPA2", is_robot_ap=False),
        ],
    )
    monkeypatch.setattr(
        rest_module.provision_backend,
        "provision_dog",
        lambda request: ProvisionResult(
            success=True,
            robot_id=request.robot_id or "logistics_01",
            dog_mac="aa:bb:cc:dd:ee:11",
            dog_ip="192.168.1.111",
            pc_ip="192.168.1.10",
            role=request.role,
            message="Provisioning complete",
        ),
    )
    rest_module._PROVISIONING_JOBS.clear()

    with TestClient(rest_module.create_app()) as client:
        scan_response = client.get("/provision/scan", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))
        assert scan_response.status_code == 200
        assert scan_response.json()[0]["is_robot_ap"] is True

        start_response = client.post(
            "/provision/start",
            headers=build_auth_header(TEST_SUPERVISOR_TOKEN),
            json={
                "dog_ap_ssid": "D1-Ultra:aa:bb:cc:dd:ee",
                "target_wifi_ssid": "FACTORY_WIFI",
                "target_wifi_password": "super-secret-password",
                "role": "logistics",
                "robot_id": "logistics_01",
                "display_name": "Logistics Robot 1",
                "pc_wifi_iface": "wlan0",
            },
        )

        assert start_response.status_code == 200
        assert "super-secret-password" not in start_response.text

        status_body = _poll_job_status(client, start_response.json()["job_id"], TEST_SUPERVISOR_TOKEN)
        assert status_body["status"] == "succeeded"
        assert status_body["robot_id"] == "logistics_01"
        assert status_body["dog_mac"] == "aa:bb:cc:dd:ee:11"
        assert status_body["dog_ip"] == "192.168.1.111"
        assert "super-secret-password" not in str(status_body)

        robots_response = client.get("/provision/robots", headers=build_auth_header(TEST_SUPERVISOR_TOKEN))
        assert robots_response.status_code == 200
        assert robots_response.json() == [
            {
                "robot_id": "logistics_01",
                "display_name": "Logistics Robot 1",
                "mac": "aa:bb:cc:dd:ee:11",
                "quadruped_ip": "192.168.1.111",
                "role": "logistics",
                "enabled": True,
            }
        ]
        assert "super-secret-password" not in robots_response.text
        assert robots_yaml_path.exists()
