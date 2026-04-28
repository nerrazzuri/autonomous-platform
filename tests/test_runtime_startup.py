from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class LifecycleStub:
    def __init__(self, name: str, calls: list[str], *, fail_on: str | None = None) -> None:
        self.name = name
        self.calls = calls
        self.fail_on = fail_on

    async def start(self) -> None:
        self.calls.append(f"start:{self.name}")
        if self.fail_on == "start":
            raise RuntimeError(f"{self.name} start failed")

    async def stop(self) -> None:
        self.calls.append(f"stop:{self.name}")
        if self.fail_on == "stop":
            raise RuntimeError(f"{self.name} stop failed")


class DatabaseStub:
    def __init__(self, calls: list[str], *, fail_on_initialize: bool = False, fail_on_close: bool = False) -> None:
        self.calls = calls
        self.fail_on_initialize = fail_on_initialize
        self.fail_on_close = fail_on_close

    async def initialize(self) -> None:
        self.calls.append("database.initialize")
        if self.fail_on_initialize:
            raise RuntimeError("database initialize failed")

    async def close(self) -> None:
        self.calls.append("database.close")
        if self.fail_on_close:
            raise RuntimeError("database close failed")


class RouteStoreStub:
    def __init__(self, calls: list[str], *, fail_on_load: bool = False) -> None:
        self.calls = calls
        self.fail_on_load = fail_on_load

    async def load(self) -> None:
        self.calls.append("route_store.load")
        if self.fail_on_load:
            raise RuntimeError("route_store load failed")


class SDKStub:
    def __init__(self, calls: list[str], *, connected: bool = True) -> None:
        self.calls = calls
        self.connected = connected

    async def connect(self) -> bool:
        self.calls.append("sdk.connect")
        return self.connected

    async def stand_up(self) -> bool:
        self.calls.append("sdk.stand_up")
        return True


class RobotSDKFactory:
    def __init__(self, calls: list[str], *, fail_on_connect_robot_ips: set[str] | None = None) -> None:
        self.calls = calls
        self.fail_on_connect_robot_ips = fail_on_connect_robot_ips or set()
        self.instances: list[RobotSDKInstance] = []

    def __call__(
        self,
        *,
        quadruped_ip: str,
        local_ip: str,
        sdk_port: int,
        sdk_lib_path: str | None = None,
    ) -> "RobotSDKInstance":
        instance = RobotSDKInstance(
            calls=self.calls,
            quadruped_ip=quadruped_ip,
            local_ip=local_ip,
            sdk_port=sdk_port,
            sdk_lib_path=sdk_lib_path,
            fail_on_connect=quadruped_ip in self.fail_on_connect_robot_ips,
        )
        self.instances.append(instance)
        return instance


class RobotSDKInstance:
    def __init__(
        self,
        *,
        calls: list[str],
        quadruped_ip: str,
        local_ip: str,
        sdk_port: int,
        sdk_lib_path: str | None,
        fail_on_connect: bool,
    ) -> None:
        self.calls = calls
        self.quadruped_ip = quadruped_ip
        self.local_ip = local_ip
        self.sdk_port = sdk_port
        self.sdk_lib_path = sdk_lib_path
        self.fail_on_connect = fail_on_connect

    async def connect(self) -> bool:
        self.calls.append(f"sdk.connect:{self.quadruped_ip}")
        if self.fail_on_connect:
            raise RuntimeError(f"{self.quadruped_ip} connect failed")
        return True

    async def stand_up(self) -> bool:
        self.calls.append(f"sdk.stand_up:{self.quadruped_ip}")
        return True

    async def disconnect(self) -> None:
        self.calls.append(f"sdk.disconnect:{self.quadruped_ip}")


class HeartbeatFactory:
    def __init__(self, calls: list[str], *, fail_on_start_robot_ids: set[str] | None = None) -> None:
        self.calls = calls
        self.fail_on_start_robot_ids = fail_on_start_robot_ids or set()
        self.instances: list[LifecycleRobotComponent] = []

    def __call__(self, *, sdk_adapter, robot_id: str) -> "LifecycleRobotComponent":
        instance = LifecycleRobotComponent(
            name="heartbeat",
            robot_id=robot_id,
            calls=self.calls,
            sdk_adapter=sdk_adapter,
            fail_on_start=robot_id in self.fail_on_start_robot_ids,
        )
        self.instances.append(instance)
        return instance


class StateMonitorFactory:
    def __init__(self, calls: list[str], *, fail_on_start_robot_ids: set[str] | None = None) -> None:
        self.calls = calls
        self.fail_on_start_robot_ids = fail_on_start_robot_ids or set()
        self.instances: list[LifecycleRobotComponent] = []

    def __call__(self, *, sdk_adapter, database, robot_id: str) -> "LifecycleRobotComponent":
        instance = LifecycleRobotComponent(
            name="state_monitor",
            robot_id=robot_id,
            calls=self.calls,
            sdk_adapter=sdk_adapter,
            database=database,
            fail_on_start=robot_id in self.fail_on_start_robot_ids,
        )
        self.instances.append(instance)
        return instance


class NavigatorFactory:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.instances: list[NavigatorStub] = []

    def __call__(self, *, sdk_adapter, robot_id: str, route_store, state_monitor, heartbeat) -> "NavigatorStub":
        instance = NavigatorStub(
            calls=self.calls,
            sdk_adapter=sdk_adapter,
            robot_id=robot_id,
            route_store=route_store,
            state_monitor=state_monitor,
            heartbeat=heartbeat,
        )
        self.instances.append(instance)
        return instance


class LifecycleRobotComponent:
    def __init__(
        self,
        *,
        name: str,
        robot_id: str,
        calls: list[str],
        sdk_adapter,
        database=None,
        fail_on_start: bool = False,
    ) -> None:
        self.name = name
        self.robot_id = robot_id
        self.calls = calls
        self.sdk_adapter = sdk_adapter
        self.database = database
        self.fail_on_start = fail_on_start

    async def start(self) -> None:
        self.calls.append(f"start:{self.name}:{self.robot_id}")
        if self.fail_on_start:
            raise RuntimeError(f"{self.name}:{self.robot_id} start failed")

    async def stop(self) -> None:
        self.calls.append(f"stop:{self.name}:{self.robot_id}")


class NavigatorStub:
    def __init__(self, *, calls: list[str], sdk_adapter, robot_id: str, route_store, state_monitor, heartbeat) -> None:
        self.calls = calls
        self.sdk_adapter = sdk_adapter
        self.robot_id = robot_id
        self.route_store = route_store
        self.state_monitor = state_monitor
        self.heartbeat = heartbeat


def write_yaml(path: Path, content: str) -> None:
    path.write_text(dedent(content).strip() + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def runtime_startup_global_isolation():
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

    base_startup_snapshot = None
    base_startup_module = sys.modules.get("shared.runtime.base_startup")
    if base_startup_module is not None and hasattr(base_startup_module, "_DEFAULT_SINGLETONS"):
        base_startup_snapshot = dict(base_startup_module._DEFAULT_SINGLETONS)

    consumer_snapshots: dict[str, dict[str, object]] = {}
    for module_name, singleton_name, attrs in (
        ("apps.logistics.tasks.dispatcher", "dispatcher", ("_navigator", "_state_monitor")),
        ("apps.logistics.tasks.battery_manager", "battery_manager", ("_dispatcher", "_state_monitor")),
        ("apps.logistics.tasks.watchdog", "watchdog", ("_dispatcher", "_state_monitor")),
        ("apps.patrol.tasks.patrol_dispatcher", "patrol_dispatcher", ("_navigator",)),
    ):
        module = sys.modules.get(module_name)
        if module is None or not hasattr(module, singleton_name):
            continue
        singleton = getattr(module, singleton_name)
        consumer_snapshots[module_name] = {
            attr_name: getattr(singleton, attr_name)
            for attr_name in attrs
            if hasattr(singleton, attr_name)
        }

    try:
        yield
    finally:
        sdk_adapter_module.sdk_adapter = singleton_snapshot["sdk_adapter"]
        heartbeat_module.heartbeat_controller = singleton_snapshot["heartbeat_controller"]
        state_monitor_module.state_monitor = singleton_snapshot["state_monitor"]
        navigator_module.navigator = singleton_snapshot["navigator"]

        if base_startup_snapshot is not None:
            current_base_startup_module = sys.modules.get("shared.runtime.base_startup")
            if current_base_startup_module is not None and hasattr(current_base_startup_module, "_DEFAULT_SINGLETONS"):
                current_base_startup_module._DEFAULT_SINGLETONS.clear()
                current_base_startup_module._DEFAULT_SINGLETONS.update(base_startup_snapshot)

        for module_name, attrs in consumer_snapshots.items():
            module = sys.modules.get(module_name)
            if module is None:
                continue
            singleton_name = module_name.rsplit(".", 1)[-1]
            if singleton_name == "dispatcher":
                target_name = "dispatcher"
            elif singleton_name == "battery_manager":
                target_name = "battery_manager"
            elif singleton_name == "watchdog":
                target_name = "watchdog"
            else:
                target_name = "patrol_dispatcher"
            if not hasattr(module, target_name):
                continue
            singleton = getattr(module, target_name)
            for attr_name, value in attrs.items():
                setattr(singleton, attr_name, value)

        registry.clear()
        for platform in registry_snapshot:
            registry.register(platform)


@pytest.fixture
def base_startup_module(runtime_startup_global_isolation):
    sys.modules.pop("shared.runtime.base_startup", None)
    return importlib.import_module("shared.runtime.base_startup")


@pytest.fixture
def logistics_startup_module(runtime_startup_global_isolation):
    sys.modules.pop("apps.logistics.runtime.startup", None)
    return importlib.import_module("apps.logistics.runtime.startup")


def test_base_startup_has_no_apps_imports(base_startup_module) -> None:
    source = Path(base_startup_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.append(node.module)

    assert not any(module == "apps" or module.startswith("apps.") for module in imported_modules)


def test_shared_runtime_startup_is_compatibility_shim(base_startup_module) -> None:
    sys.modules.pop("shared.runtime.startup", None)
    startup_module = importlib.import_module("shared.runtime.startup")

    assert startup_module.startup_system is base_startup_module.startup_system
    assert startup_module.shutdown_system is base_startup_module.shutdown_system


@pytest.mark.asyncio
async def test_base_startup_starts_shared_services_in_order(
    base_startup_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(base_startup_module, "setup_logging", lambda: calls.append("setup_logging"))
    monkeypatch.setattr(
        base_startup_module,
        "get_config",
        lambda: SimpleNamespace(
            api=SimpleNamespace(host="0.0.0.0", port=8080),
            quadruped=SimpleNamespace(auto_stand_on_startup=False),
        ),
    )
    monkeypatch.setattr(base_startup_module, "get_database", lambda: DatabaseStub(calls))
    monkeypatch.setattr(base_startup_module, "get_route_store", lambda: RouteStoreStub(calls))
    monkeypatch.setattr(base_startup_module, "get_event_bus", lambda: LifecycleStub("event_bus", calls))
    monkeypatch.setattr(base_startup_module, "get_sdk_adapter", lambda: SDKStub(calls))
    monkeypatch.setattr(base_startup_module, "get_heartbeat_controller", lambda: LifecycleStub("heartbeat", calls))
    monkeypatch.setattr(base_startup_module, "get_state_monitor", lambda: LifecycleStub("state_monitor", calls))
    monkeypatch.setattr(base_startup_module, "get_obstacle_detector", lambda: LifecycleStub("obstacle", calls))
    monkeypatch.setattr(base_startup_module, "_load_enabled_robot_configs", lambda _config: [])

    await base_startup_module.startup_system()

    assert calls == [
        "setup_logging",
        "database.initialize",
        "route_store.load",
        "start:event_bus",
        "sdk.connect",
        "start:heartbeat",
        "start:state_monitor",
        "start:obstacle",
    ]


@pytest.mark.asyncio
async def test_base_shutdown_stops_shared_services_in_reverse_order(
    base_startup_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(base_startup_module, "get_obstacle_detector", lambda: LifecycleStub("obstacle", calls))
    monkeypatch.setattr(base_startup_module, "get_state_monitor", lambda: LifecycleStub("state_monitor", calls))
    monkeypatch.setattr(base_startup_module, "get_heartbeat_controller", lambda: LifecycleStub("heartbeat", calls))
    monkeypatch.setattr(base_startup_module, "get_event_bus", lambda: LifecycleStub("event_bus", calls))
    monkeypatch.setattr(base_startup_module, "get_database", lambda: DatabaseStub(calls))
    monkeypatch.setattr(base_startup_module, "get_robot_registry", lambda: SimpleNamespace(all=lambda: []))

    await base_startup_module.shutdown_system()

    assert calls == [
        "stop:obstacle",
        "stop:state_monitor",
        "stop:heartbeat",
        "stop:event_bus",
        "database.close",
    ]


@pytest.mark.asyncio
async def test_base_startup_registers_two_enabled_robot_platforms(
    base_startup_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from shared.navigation.navigator import get_navigator
    from shared.quadruped.heartbeat import get_heartbeat_controller
    from shared.quadruped.robot_registry import RobotRegistry
    from shared.quadruped.sdk_adapter import get_sdk_adapter
    from shared.quadruped.state_monitor import get_state_monitor

    calls: list[str] = []
    registry = RobotRegistry()
    sdk_factory = RobotSDKFactory(calls)
    heartbeat_factory = HeartbeatFactory(calls)
    state_monitor_factory = StateMonitorFactory(calls)
    navigator_factory = NavigatorFactory(calls)
    robots_path = tmp_path / "robots.yaml"
    write_yaml(
        robots_path,
        """
        robots:
          - robot_id: robot_01
            enabled: true
            connection:
              robot_ip: 192.168.1.101
              sdk_port: 43988
              local_ip: 192.168.1.10
              local_port: 50051
          - robot_id: robot_02
            enabled: true
            connection:
              robot_ip: 192.168.1.102
              sdk_port: 43988
              local_ip: 192.168.1.10
              local_port: 50052
        """,
    )

    monkeypatch.setattr(base_startup_module, "setup_logging", lambda: calls.append("setup_logging"))
    monkeypatch.setattr(
        base_startup_module,
        "get_config",
        lambda: SimpleNamespace(quadruped=SimpleNamespace(auto_stand_on_startup=False, sdk_lib_path=None)),
    )
    monkeypatch.setattr(base_startup_module, "_resolve_robot_config_path", lambda _config: robots_path)
    monkeypatch.setattr(base_startup_module, "get_database", lambda: DatabaseStub(calls))
    monkeypatch.setattr(base_startup_module, "get_route_store", lambda: RouteStoreStub(calls))
    monkeypatch.setattr(base_startup_module, "get_event_bus", lambda: LifecycleStub("event_bus", calls))
    monkeypatch.setattr(base_startup_module, "get_obstacle_detector", lambda: LifecycleStub("obstacle", calls))
    monkeypatch.setattr(base_startup_module, "get_robot_registry", lambda: registry)
    monkeypatch.setattr(base_startup_module, "SDKAdapter", sdk_factory)
    monkeypatch.setattr(base_startup_module, "HeartbeatController", heartbeat_factory)
    monkeypatch.setattr(base_startup_module, "StateMonitor", state_monitor_factory)
    monkeypatch.setattr(base_startup_module, "Navigator", navigator_factory)

    await base_startup_module.startup_system()

    robot_01 = registry.get("robot_01")
    robot_02 = registry.get("robot_02")
    assert registry.count() == 2
    assert robot_01.sdk_adapter is not robot_02.sdk_adapter
    assert robot_01.heartbeat is not robot_02.heartbeat
    assert robot_01.state_monitor is not robot_02.state_monitor
    assert robot_01.navigator is not robot_02.navigator
    assert get_sdk_adapter() is robot_01.sdk_adapter
    assert get_heartbeat_controller() is robot_01.heartbeat
    assert get_state_monitor() is robot_01.state_monitor
    assert get_navigator() is robot_01.navigator


@pytest.mark.asyncio
async def test_base_startup_skips_disabled_robot(
    base_startup_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from shared.quadruped.robot_registry import RobotRegistry

    calls: list[str] = []
    registry = RobotRegistry()
    robots_path = tmp_path / "robots.yaml"
    write_yaml(
        robots_path,
        """
        robots:
          - robot_id: robot_01
            enabled: true
            connection:
              robot_ip: 192.168.1.101
              sdk_port: 43988
              local_ip: 192.168.1.10
              local_port: 50051
          - robot_id: robot_02
            enabled: false
            connection:
              robot_ip: 192.168.1.102
              sdk_port: 43988
              local_ip: 192.168.1.10
              local_port: 50052
        """,
    )

    monkeypatch.setattr(base_startup_module, "setup_logging", lambda: calls.append("setup_logging"))
    monkeypatch.setattr(
        base_startup_module,
        "get_config",
        lambda: SimpleNamespace(quadruped=SimpleNamespace(auto_stand_on_startup=False, sdk_lib_path=None)),
    )
    monkeypatch.setattr(base_startup_module, "_resolve_robot_config_path", lambda _config: robots_path)
    monkeypatch.setattr(base_startup_module, "get_database", lambda: DatabaseStub(calls))
    monkeypatch.setattr(base_startup_module, "get_route_store", lambda: RouteStoreStub(calls))
    monkeypatch.setattr(base_startup_module, "get_event_bus", lambda: LifecycleStub("event_bus", calls))
    monkeypatch.setattr(base_startup_module, "get_obstacle_detector", lambda: LifecycleStub("obstacle", calls))
    monkeypatch.setattr(base_startup_module, "get_robot_registry", lambda: registry)
    monkeypatch.setattr(base_startup_module, "SDKAdapter", RobotSDKFactory(calls))
    monkeypatch.setattr(base_startup_module, "HeartbeatController", HeartbeatFactory(calls))
    monkeypatch.setattr(base_startup_module, "StateMonitor", StateMonitorFactory(calls))
    monkeypatch.setattr(base_startup_module, "Navigator", NavigatorFactory(calls))

    await base_startup_module.startup_system()

    assert registry.is_registered("robot_01") is True
    assert registry.is_registered("robot_02") is False


@pytest.mark.asyncio
async def test_base_startup_rolls_back_when_second_robot_fails(
    base_startup_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from shared.quadruped.robot_registry import RobotRegistry

    calls: list[str] = []
    registry = RobotRegistry()
    sdk_factory = RobotSDKFactory(calls)
    heartbeat_factory = HeartbeatFactory(calls, fail_on_start_robot_ids={"robot_02"})
    robots_path = tmp_path / "robots.yaml"
    write_yaml(
        robots_path,
        """
        robots:
          - robot_id: robot_01
            enabled: true
            connection:
              robot_ip: 192.168.1.101
              sdk_port: 43988
              local_ip: 192.168.1.10
              local_port: 50051
          - robot_id: robot_02
            enabled: true
            connection:
              robot_ip: 192.168.1.102
              sdk_port: 43988
              local_ip: 192.168.1.10
              local_port: 50052
        """,
    )

    monkeypatch.setattr(base_startup_module, "setup_logging", lambda: calls.append("setup_logging"))
    monkeypatch.setattr(
        base_startup_module,
        "get_config",
        lambda: SimpleNamespace(quadruped=SimpleNamespace(auto_stand_on_startup=False, sdk_lib_path=None)),
    )
    monkeypatch.setattr(base_startup_module, "_resolve_robot_config_path", lambda _config: robots_path)
    monkeypatch.setattr(base_startup_module, "get_database", lambda: DatabaseStub(calls))
    monkeypatch.setattr(base_startup_module, "get_route_store", lambda: RouteStoreStub(calls))
    monkeypatch.setattr(base_startup_module, "get_event_bus", lambda: LifecycleStub("event_bus", calls))
    monkeypatch.setattr(base_startup_module, "get_obstacle_detector", lambda: LifecycleStub("obstacle", calls))
    monkeypatch.setattr(base_startup_module, "get_robot_registry", lambda: registry)
    monkeypatch.setattr(base_startup_module, "SDKAdapter", sdk_factory)
    monkeypatch.setattr(base_startup_module, "HeartbeatController", heartbeat_factory)
    monkeypatch.setattr(base_startup_module, "StateMonitor", StateMonitorFactory(calls))
    monkeypatch.setattr(base_startup_module, "Navigator", NavigatorFactory(calls))

    with pytest.raises(RuntimeError, match="heartbeat:robot_02 start failed"):
        await base_startup_module.startup_system()

    assert registry.count() == 0
    assert "stop:heartbeat:robot_01" in calls
    assert "stop:state_monitor:robot_01" in calls
    assert "sdk.disconnect:192.168.1.101" in calls
    assert "sdk.disconnect:192.168.1.102" in calls


@pytest.mark.asyncio
async def test_base_shutdown_stops_all_registered_robot_platforms(
    base_startup_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from shared.quadruped.robot_registry import RobotRegistry

    calls: list[str] = []
    registry = RobotRegistry()
    robots_path = tmp_path / "robots.yaml"
    write_yaml(
        robots_path,
        """
        robots:
          - robot_id: robot_01
            enabled: true
            connection:
              robot_ip: 192.168.1.101
              sdk_port: 43988
              local_ip: 192.168.1.10
              local_port: 50051
          - robot_id: robot_02
            enabled: true
            connection:
              robot_ip: 192.168.1.102
              sdk_port: 43988
              local_ip: 192.168.1.10
              local_port: 50052
        """,
    )

    monkeypatch.setattr(base_startup_module, "setup_logging", lambda: calls.append("setup_logging"))
    monkeypatch.setattr(
        base_startup_module,
        "get_config",
        lambda: SimpleNamespace(quadruped=SimpleNamespace(auto_stand_on_startup=False, sdk_lib_path=None)),
    )
    monkeypatch.setattr(base_startup_module, "_resolve_robot_config_path", lambda _config: robots_path)
    monkeypatch.setattr(base_startup_module, "get_database", lambda: DatabaseStub(calls))
    monkeypatch.setattr(base_startup_module, "get_route_store", lambda: RouteStoreStub(calls))
    monkeypatch.setattr(base_startup_module, "get_event_bus", lambda: LifecycleStub("event_bus", calls))
    monkeypatch.setattr(base_startup_module, "get_obstacle_detector", lambda: LifecycleStub("obstacle", calls))
    monkeypatch.setattr(base_startup_module, "get_robot_registry", lambda: registry)
    monkeypatch.setattr(base_startup_module, "SDKAdapter", RobotSDKFactory(calls))
    monkeypatch.setattr(base_startup_module, "HeartbeatController", HeartbeatFactory(calls))
    monkeypatch.setattr(base_startup_module, "StateMonitor", StateMonitorFactory(calls))
    monkeypatch.setattr(base_startup_module, "Navigator", NavigatorFactory(calls))

    await base_startup_module.startup_system()
    calls.clear()

    await base_startup_module.shutdown_system()

    assert registry.count() == 0
    assert "stop:obstacle" in calls
    assert "stop:heartbeat:robot_01" in calls
    assert "stop:heartbeat:robot_02" in calls
    assert "stop:state_monitor:robot_01" in calls
    assert "stop:state_monitor:robot_02" in calls
    assert "sdk.disconnect:192.168.1.101" in calls
    assert "sdk.disconnect:192.168.1.102" in calls


@pytest.mark.asyncio
async def test_logistics_startup_starts_base_then_logistics_services(
    logistics_startup_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def startup_base() -> None:
        calls.append("base.startup")

    monkeypatch.setattr(logistics_startup_module.base_startup, "startup_system", startup_base)
    monkeypatch.setattr(logistics_startup_module, "get_dispatcher", lambda: LifecycleStub("dispatcher", calls))
    monkeypatch.setattr(logistics_startup_module, "get_battery_manager", lambda: LifecycleStub("battery", calls))
    monkeypatch.setattr(logistics_startup_module, "get_watchdog", lambda: LifecycleStub("watchdog", calls))

    await logistics_startup_module.startup_system()

    assert calls == [
        "base.startup",
        "start:dispatcher",
        "start:battery",
        "start:watchdog",
    ]


@pytest.mark.asyncio
async def test_logistics_shutdown_stops_logistics_then_base(
    logistics_startup_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def shutdown_base() -> None:
        calls.append("base.shutdown")

    monkeypatch.setattr(logistics_startup_module.base_startup, "shutdown_system", shutdown_base)
    monkeypatch.setattr(logistics_startup_module, "get_dispatcher", lambda: LifecycleStub("dispatcher", calls))
    monkeypatch.setattr(logistics_startup_module, "get_battery_manager", lambda: LifecycleStub("battery", calls))
    monkeypatch.setattr(logistics_startup_module, "get_watchdog", lambda: LifecycleStub("watchdog", calls))

    await logistics_startup_module.shutdown_system()

    assert calls == [
        "stop:watchdog",
        "stop:battery",
        "stop:dispatcher",
        "base.shutdown",
    ]


@pytest.mark.asyncio
async def test_logistics_startup_failure_shuts_down_started_components(
    logistics_startup_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def startup_base() -> None:
        calls.append("base.startup")

    async def shutdown_base() -> None:
        calls.append("base.shutdown")

    monkeypatch.setattr(logistics_startup_module.base_startup, "startup_system", startup_base)
    monkeypatch.setattr(logistics_startup_module.base_startup, "shutdown_system", shutdown_base)
    monkeypatch.setattr(logistics_startup_module, "get_dispatcher", lambda: LifecycleStub("dispatcher", calls))
    monkeypatch.setattr(
        logistics_startup_module,
        "get_battery_manager",
        lambda: LifecycleStub("battery", calls, fail_on="start"),
    )
    monkeypatch.setattr(logistics_startup_module, "get_watchdog", lambda: LifecycleStub("watchdog", calls))

    with pytest.raises(RuntimeError, match="battery start failed"):
        await logistics_startup_module.startup_system()

    assert calls == [
        "base.startup",
        "start:dispatcher",
        "start:battery",
        "stop:dispatcher",
        "base.shutdown",
    ]
