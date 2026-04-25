from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path
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


@pytest.fixture
def base_startup_module():
    sys.modules.pop("shared.runtime.base_startup", None)
    return importlib.import_module("shared.runtime.base_startup")


@pytest.fixture
def logistics_startup_module():
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

    await base_startup_module.shutdown_system()

    assert calls == [
        "stop:obstacle",
        "stop:state_monitor",
        "stop:heartbeat",
        "stop:event_bus",
        "database.close",
    ]


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
