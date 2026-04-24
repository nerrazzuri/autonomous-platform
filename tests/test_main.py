from __future__ import annotations

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

    async def stop(self) -> None:
        self.calls.append(f"stop:{self.name}")
        if self.fail_on == "stop":
            raise RuntimeError(f"{self.name} stop failed")


class DatabaseStub:
    def __init__(self, calls: list[str], *, fail_on_close: bool = False) -> None:
        self.calls = calls
        self.fail_on_close = fail_on_close

    async def initialize(self) -> None:
        self.calls.append("database.initialize")

    async def close(self) -> None:
        self.calls.append("database.close")
        if self.fail_on_close:
            raise RuntimeError("database close failed")


class RouteStoreStub:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def load(self) -> None:
        self.calls.append("route_store.load")


class SDKStub:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def connect(self) -> bool:
        self.calls.append("sdk.connect")
        return True

    async def stand_up(self) -> bool:
        self.calls.append("sdk.stand_up")
        return True


@pytest.fixture
def main_module(monkeypatch: pytest.MonkeyPatch):
    sys.modules.pop("main", None)
    module = importlib.import_module("main")
    return module


def test_create_uvicorn_config(main_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main_module,
        "get_config",
        lambda: SimpleNamespace(api=SimpleNamespace(host="127.0.0.1", port=9090)),
    )

    config = main_module.create_uvicorn_config()

    assert config == {
        "app": "api.rest:app",
        "host": "127.0.0.1",
        "port": 9090,
        "reload": False,
    }


@pytest.mark.asyncio
async def test_startup_system_starts_components(main_module, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    database = DatabaseStub(calls)
    route_store = RouteStoreStub(calls)
    event_bus = LifecycleStub("event_bus", calls)
    heartbeat = LifecycleStub("heartbeat", calls)
    state_monitor = LifecycleStub("state_monitor", calls)
    obstacle = LifecycleStub("obstacle", calls)
    dispatcher = LifecycleStub("dispatcher", calls)
    battery = LifecycleStub("battery", calls)
    watchdog = LifecycleStub("watchdog", calls)
    sdk = SDKStub(calls)

    monkeypatch.setattr(main_module, "setup_logging", lambda: calls.append("setup_logging"))
    monkeypatch.setattr(
        main_module,
        "get_config",
        lambda: SimpleNamespace(
            api=SimpleNamespace(host="0.0.0.0", port=8080),
            quadruped=SimpleNamespace(auto_stand_on_startup=False),
        ),
    )
    monkeypatch.setattr(main_module, "get_database", lambda: database)
    monkeypatch.setattr(main_module, "get_route_store", lambda: route_store)
    monkeypatch.setattr(main_module, "get_event_bus", lambda: event_bus)
    monkeypatch.setattr(main_module, "get_heartbeat_controller", lambda: heartbeat)
    monkeypatch.setattr(main_module, "get_state_monitor", lambda: state_monitor)
    monkeypatch.setattr(main_module, "get_obstacle_detector", lambda: obstacle)
    monkeypatch.setattr(main_module, "get_dispatcher", lambda: dispatcher)
    monkeypatch.setattr(main_module, "get_battery_manager", lambda: battery)
    monkeypatch.setattr(main_module, "get_watchdog", lambda: watchdog)
    monkeypatch.setattr(main_module, "get_sdk_adapter", lambda: sdk)

    await main_module.startup_system()

    assert calls == [
        "setup_logging",
        "database.initialize",
        "route_store.load",
        "start:event_bus",
        "start:heartbeat",
        "start:state_monitor",
        "start:obstacle",
        "start:dispatcher",
        "start:battery",
        "start:watchdog",
    ]


@pytest.mark.asyncio
async def test_startup_system_auto_stands_when_enabled(main_module, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(main_module, "setup_logging", lambda: calls.append("setup_logging"))
    monkeypatch.setattr(
        main_module,
        "get_config",
        lambda: SimpleNamespace(
            api=SimpleNamespace(host="0.0.0.0", port=8080),
            quadruped=SimpleNamespace(auto_stand_on_startup=True),
        ),
    )
    monkeypatch.setattr(main_module, "get_database", lambda: DatabaseStub(calls))
    monkeypatch.setattr(main_module, "get_route_store", lambda: RouteStoreStub(calls))
    monkeypatch.setattr(main_module, "get_event_bus", lambda: LifecycleStub("event_bus", calls))
    monkeypatch.setattr(main_module, "get_heartbeat_controller", lambda: LifecycleStub("heartbeat", calls))
    monkeypatch.setattr(main_module, "get_state_monitor", lambda: LifecycleStub("state_monitor", calls))
    monkeypatch.setattr(main_module, "get_obstacle_detector", lambda: LifecycleStub("obstacle", calls))
    monkeypatch.setattr(main_module, "get_dispatcher", lambda: LifecycleStub("dispatcher", calls))
    monkeypatch.setattr(main_module, "get_battery_manager", lambda: LifecycleStub("battery", calls))
    monkeypatch.setattr(main_module, "get_watchdog", lambda: LifecycleStub("watchdog", calls))
    monkeypatch.setattr(main_module, "get_sdk_adapter", lambda: SDKStub(calls))

    await main_module.startup_system()

    assert calls[-2:] == ["sdk.connect", "sdk.stand_up"]


@pytest.mark.asyncio
async def test_shutdown_system_stops_components_in_reverse_order(
    main_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(main_module, "get_watchdog", lambda: LifecycleStub("watchdog", calls))
    monkeypatch.setattr(main_module, "get_battery_manager", lambda: LifecycleStub("battery", calls))
    monkeypatch.setattr(main_module, "get_dispatcher", lambda: LifecycleStub("dispatcher", calls))
    monkeypatch.setattr(main_module, "get_obstacle_detector", lambda: LifecycleStub("obstacle", calls))
    monkeypatch.setattr(main_module, "get_state_monitor", lambda: LifecycleStub("state_monitor", calls))
    monkeypatch.setattr(main_module, "get_heartbeat_controller", lambda: LifecycleStub("heartbeat", calls))
    monkeypatch.setattr(main_module, "get_event_bus", lambda: LifecycleStub("event_bus", calls))
    monkeypatch.setattr(main_module, "get_database", lambda: DatabaseStub(calls))

    await main_module.shutdown_system()

    assert calls == [
        "stop:watchdog",
        "stop:battery",
        "stop:dispatcher",
        "stop:obstacle",
        "stop:state_monitor",
        "stop:heartbeat",
        "stop:event_bus",
        "database.close",
    ]


@pytest.mark.asyncio
async def test_shutdown_system_continues_after_failure(main_module, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(main_module, "get_watchdog", lambda: LifecycleStub("watchdog", calls))
    monkeypatch.setattr(main_module, "get_battery_manager", lambda: LifecycleStub("battery", calls, fail_on="stop"))
    monkeypatch.setattr(main_module, "get_dispatcher", lambda: LifecycleStub("dispatcher", calls))
    monkeypatch.setattr(main_module, "get_obstacle_detector", lambda: LifecycleStub("obstacle", calls))
    monkeypatch.setattr(main_module, "get_state_monitor", lambda: LifecycleStub("state_monitor", calls))
    monkeypatch.setattr(main_module, "get_heartbeat_controller", lambda: LifecycleStub("heartbeat", calls))
    monkeypatch.setattr(main_module, "get_event_bus", lambda: LifecycleStub("event_bus", calls))
    monkeypatch.setattr(main_module, "get_database", lambda: DatabaseStub(calls, fail_on_close=True))

    await main_module.shutdown_system()

    assert calls == [
        "stop:watchdog",
        "stop:battery",
        "stop:dispatcher",
        "stop:obstacle",
        "stop:state_monitor",
        "stop:heartbeat",
        "stop:event_bus",
        "database.close",
    ]


def test_main_importable_without_running_server(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeUvicorn:
        def run(self, **kwargs):
            raise AssertionError("uvicorn.run should not execute during import")

    sys.modules.pop("main", None)
    monkeypatch.setitem(sys.modules, "uvicorn", FakeUvicorn())

    module = importlib.import_module("main")

    assert callable(module.main)
    assert callable(module.startup_system)
    assert callable(module.shutdown_system)

