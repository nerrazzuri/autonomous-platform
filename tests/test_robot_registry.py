from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from shared.core.robot_config import RobotCapabilityConfig, RobotConfig, RobotConnectionConfig
from shared.quadruped.robot_platform import RobotPlatform


ROOT = Path(__file__).resolve().parents[1]


def load_robot_registry_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("shared.quadruped.robot_registry")


@pytest.fixture(autouse=True)
def clear_global_robot_registry() -> None:
    module = importlib.import_module("shared.quadruped.robot_registry")
    module.robot_registry.clear()
    yield
    module.robot_registry.clear()


def make_robot_platform(robot_id: str = "R1") -> RobotPlatform:
    return RobotPlatform(
        robot_id=robot_id,
        config=RobotConfig(
            connection=RobotConnectionConfig(
                robot_id=robot_id,
                robot_ip="192.168.1.101",
                sdk_port=43988,
                local_ip="192.168.1.10",
                local_port=50051,
            ),
            capabilities=RobotCapabilityConfig(
                lidar=True,
                camera=True,
                speaker=False,
                screen=False,
            ),
        ),
        sdk_adapter=object(),
        heartbeat=object(),
        state_monitor=object(),
        navigator=object(),
    )


def test_register_and_get_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_registry_module(monkeypatch)
    registry = module.RobotRegistry()
    platform = make_robot_platform()

    registry.register(platform)

    assert registry.get("R1") is platform
    assert registry.count() == 1


def test_register_duplicate_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_registry_module(monkeypatch)
    registry = module.RobotRegistry()
    platform = make_robot_platform()

    registry.register(platform)

    with pytest.raises(module.RobotAlreadyRegisteredError, match="R1"):
        registry.register(platform)


def test_register_invalid_platform_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_registry_module(monkeypatch)
    registry = module.RobotRegistry()

    with pytest.raises(module.RobotRegistryError, match="RobotPlatform"):
        registry.register(object())


def test_get_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_registry_module(monkeypatch)
    registry = module.RobotRegistry()

    with pytest.raises(module.RobotNotFoundError, match="missing"):
        registry.get("missing")
    with pytest.raises(module.RobotNotFoundError):
        registry.get("")


def test_remove_registered_robot(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_registry_module(monkeypatch)
    registry = module.RobotRegistry()
    platform = make_robot_platform()
    registry.register(platform)

    registry.remove("R1")

    assert registry.count() == 0
    assert registry.is_registered("R1") is False


def test_remove_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_registry_module(monkeypatch)
    registry = module.RobotRegistry()

    with pytest.raises(module.RobotNotFoundError, match="missing"):
        registry.remove("missing")


def test_clear_removes_all(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_registry_module(monkeypatch)
    registry = module.RobotRegistry()
    registry.register(make_robot_platform("R1"))
    registry.register(make_robot_platform("R2"))

    registry.clear()

    assert registry.count() == 0
    assert registry.all() == []


def test_is_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_registry_module(monkeypatch)
    registry = module.RobotRegistry()
    registry.register(make_robot_platform("R1"))

    assert registry.is_registered("R1") is True
    assert registry.is_registered("R2") is False
    assert registry.is_registered("") is False


def test_all_returns_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_registry_module(monkeypatch)
    registry = module.RobotRegistry()
    platform = make_robot_platform("R1")
    registry.register(platform)

    platforms = registry.all()
    platforms.clear()

    assert registry.count() == 1
    assert registry.get("R1") is platform


def test_get_by_role_safe_when_role_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_registry_module(monkeypatch)
    registry = module.RobotRegistry()
    registry.register(make_robot_platform("R1"))

    assert registry.get_by_role("operator") == []
    assert registry.get_by_role("") == []


def test_global_get_robot_registry_returns_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_registry_module(monkeypatch)

    registry = module.get_robot_registry()

    assert isinstance(registry, module.RobotRegistry)
    assert registry is module.robot_registry
