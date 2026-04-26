from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
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


class ZoneConfigStub:
    def __init__(self, calls: list[str], *, fail_on_load: bool = False) -> None:
        self.calls = calls
        self.fail_on_load = fail_on_load

    async def load(self) -> None:
        self.calls.append("zone_config.load")
        if self.fail_on_load:
            raise RuntimeError("zone_config load failed")


class PatrolQueueStub:
    def __init__(self, calls: list[str], *, fail_on_initialize: bool = False) -> None:
        self.calls = calls
        self.fail_on_initialize = fail_on_initialize

    async def initialize(self) -> None:
        self.calls.append("patrol_queue.initialize")
        if self.fail_on_initialize:
            raise RuntimeError("patrol_queue initialize failed")


@pytest.fixture
def patrol_startup_module():
    sys.modules.pop("apps.patrol.runtime.startup", None)
    return importlib.import_module("apps.patrol.runtime.startup")


def test_create_uvicorn_config_targets_patrol_app(patrol_startup_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        patrol_startup_module,
        "get_config",
        lambda: SimpleNamespace(api=SimpleNamespace(host="127.0.0.1", patrol_port=8091)),
    )

    config = patrol_startup_module.create_uvicorn_config()

    assert config == {
        "app": "apps.patrol.api.rest:app",
        "host": "127.0.0.1",
        "port": 8091,
        "reload": False,
    }


@pytest.mark.asyncio
async def test_startup_system_starts_base_then_patrol_services_in_order(
    patrol_startup_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(patrol_startup_module, "setup_logging", lambda: calls.append("setup_logging"))

    async def base_startup() -> None:
        calls.append("base.startup")

    monkeypatch.setattr(patrol_startup_module.base_startup, "startup_system", base_startup)
    monkeypatch.setattr(patrol_startup_module, "get_zone_config", lambda: ZoneConfigStub(calls))
    monkeypatch.setattr(patrol_startup_module, "get_patrol_queue", lambda: PatrolQueueStub(calls))
    monkeypatch.setattr(patrol_startup_module, "get_patrol_scheduler", lambda: LifecycleStub("patrol_scheduler", calls))
    monkeypatch.setattr(patrol_startup_module, "get_patrol_dispatcher", lambda: LifecycleStub("patrol_dispatcher", calls))
    monkeypatch.setattr(patrol_startup_module, "get_patrol_watchdog", lambda: LifecycleStub("patrol_watchdog", calls))

    await patrol_startup_module.startup_system()

    assert calls == [
        "setup_logging",
        "base.startup",
        "zone_config.load",
        "patrol_queue.initialize",
        "start:patrol_scheduler",
        "start:patrol_dispatcher",
        "start:patrol_watchdog",
    ]


@pytest.mark.asyncio
async def test_shutdown_system_stops_patrol_then_base_in_order(
    patrol_startup_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def base_shutdown() -> None:
        calls.append("base.shutdown")

    monkeypatch.setattr(patrol_startup_module.base_startup, "shutdown_system", base_shutdown)
    monkeypatch.setattr(patrol_startup_module, "get_patrol_scheduler", lambda: LifecycleStub("patrol_scheduler", calls))
    monkeypatch.setattr(patrol_startup_module, "get_patrol_dispatcher", lambda: LifecycleStub("patrol_dispatcher", calls))
    monkeypatch.setattr(patrol_startup_module, "get_patrol_watchdog", lambda: LifecycleStub("patrol_watchdog", calls))

    await patrol_startup_module.shutdown_system()

    assert calls == [
        "stop:patrol_watchdog",
        "stop:patrol_dispatcher",
        "stop:patrol_scheduler",
        "base.shutdown",
    ]


@pytest.mark.asyncio
async def test_startup_failure_rolls_back_started_services_and_base(
    patrol_startup_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def base_startup() -> None:
        calls.append("base.startup")

    async def base_shutdown() -> None:
        calls.append("base.shutdown")

    monkeypatch.setattr(patrol_startup_module, "setup_logging", lambda: calls.append("setup_logging"))
    monkeypatch.setattr(patrol_startup_module.base_startup, "startup_system", base_startup)
    monkeypatch.setattr(patrol_startup_module.base_startup, "shutdown_system", base_shutdown)
    monkeypatch.setattr(patrol_startup_module, "get_zone_config", lambda: ZoneConfigStub(calls))
    monkeypatch.setattr(patrol_startup_module, "get_patrol_queue", lambda: PatrolQueueStub(calls))
    monkeypatch.setattr(patrol_startup_module, "get_patrol_scheduler", lambda: LifecycleStub("patrol_scheduler", calls))
    monkeypatch.setattr(
        patrol_startup_module,
        "get_patrol_dispatcher",
        lambda: LifecycleStub("patrol_dispatcher", calls, fail_on="start"),
    )
    monkeypatch.setattr(patrol_startup_module, "get_patrol_watchdog", lambda: LifecycleStub("patrol_watchdog", calls))

    with pytest.raises(RuntimeError, match="patrol_dispatcher start failed"):
        await patrol_startup_module.startup_system()

    assert calls == [
        "setup_logging",
        "base.startup",
        "zone_config.load",
        "patrol_queue.initialize",
        "start:patrol_scheduler",
        "start:patrol_dispatcher",
        "stop:patrol_scheduler",
        "base.shutdown",
    ]


@pytest.mark.asyncio
async def test_shutdown_continues_after_failure(
    patrol_startup_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def base_shutdown() -> None:
        calls.append("base.shutdown")

    monkeypatch.setattr(patrol_startup_module.base_startup, "shutdown_system", base_shutdown)
    monkeypatch.setattr(
        patrol_startup_module,
        "get_patrol_watchdog",
        lambda: LifecycleStub("patrol_watchdog", calls, fail_on="stop"),
    )
    monkeypatch.setattr(patrol_startup_module, "get_patrol_dispatcher", lambda: LifecycleStub("patrol_dispatcher", calls))
    monkeypatch.setattr(patrol_startup_module, "get_patrol_scheduler", lambda: LifecycleStub("patrol_scheduler", calls))

    await patrol_startup_module.shutdown_system()

    assert calls == [
        "stop:patrol_watchdog",
        "stop:patrol_dispatcher",
        "stop:patrol_scheduler",
        "base.shutdown",
    ]


def test_importing_startup_does_not_start_server(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.modules.pop("apps.patrol.runtime.startup", None)

    class FakeUvicorn:
        @staticmethod
        def run(**_kwargs):
            raise AssertionError("uvicorn.run should not execute during import")

    monkeypatch.setitem(sys.modules, "uvicorn", FakeUvicorn)
    module = importlib.import_module("apps.patrol.runtime.startup")

    assert callable(module.startup_system)
    assert callable(module.shutdown_system)
    assert callable(module.create_uvicorn_config)
    assert callable(module.main)


def test_no_logistics_imports_in_patrol_startup_source(patrol_startup_module) -> None:
    source = Path(patrol_startup_module.__file__).read_text(encoding="utf-8")

    assert "apps.logistics" not in source
