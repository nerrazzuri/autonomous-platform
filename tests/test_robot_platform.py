from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from shared.core.robot_config import RobotCapabilityConfig, RobotConfig, RobotConnectionConfig


ROOT = Path(__file__).resolve().parents[1]


def load_robot_platform_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("shared.quadruped.robot_platform")


def make_robot_config(robot_id: str = "R1") -> RobotConfig:
    return RobotConfig(
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
    )


def test_robot_platform_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_platform_module(monkeypatch)
    config = make_robot_config()
    sdk_adapter = object()
    heartbeat = object()
    state_monitor = object()
    navigator = object()

    platform = module.RobotPlatform(
        robot_id="R1",
        config=config,
        sdk_adapter=sdk_adapter,
        heartbeat=heartbeat,
        state_monitor=state_monitor,
        navigator=navigator,
    )

    assert platform.robot_id == "R1"
    assert platform.config is config
    assert platform.sdk_adapter is sdk_adapter
    assert platform.heartbeat is heartbeat
    assert platform.state_monitor is state_monitor
    assert platform.navigator is navigator


def test_robot_platform_to_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_platform_module(monkeypatch)

    platform = module.RobotPlatform(
        robot_id="R1",
        config=make_robot_config(),
        sdk_adapter=object(),
        heartbeat=object(),
        state_monitor=object(),
        navigator=object(),
    )

    result = platform.to_dict()

    assert result == {
        "robot_id": "R1",
        "robot_ip": "192.168.1.101",
        "sdk_port": 43988,
        "local_ip": "192.168.1.10",
        "local_port": 50051,
        "capabilities": {
            "lidar": True,
            "camera": True,
            "speaker": False,
            "screen": False,
        },
    }
    assert "sdk_adapter" not in result
    assert "heartbeat" not in result
    assert "state_monitor" not in result
    assert "navigator" not in result


def test_robot_platform_rejects_empty_robot_id(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_platform_module(monkeypatch)

    with pytest.raises(module.RobotPlatformError, match="robot_id"):
        module.RobotPlatform(
            robot_id="",
            config=make_robot_config(),
            sdk_adapter=object(),
            heartbeat=object(),
            state_monitor=object(),
            navigator=object(),
        )


def test_robot_platform_rejects_config_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_platform_module(monkeypatch)

    with pytest.raises(module.RobotPlatformError, match="robot_id"):
        module.RobotPlatform(
            robot_id="R2",
            config=make_robot_config("R1"),
            sdk_adapter=object(),
            heartbeat=object(),
            state_monitor=object(),
            navigator=object(),
        )


@pytest.mark.parametrize("field_name", ["sdk_adapter", "heartbeat", "state_monitor", "navigator"])
def test_robot_platform_rejects_missing_components(
    monkeypatch: pytest.MonkeyPatch, field_name: str
) -> None:
    module = load_robot_platform_module(monkeypatch)
    kwargs = {
        "robot_id": "R1",
        "config": make_robot_config(),
        "sdk_adapter": object(),
        "heartbeat": object(),
        "state_monitor": object(),
        "navigator": object(),
    }
    kwargs[field_name] = None

    with pytest.raises(module.RobotPlatformError, match=field_name):
        module.RobotPlatform(**kwargs)


def test_robot_platform_rejects_invalid_config_type(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_platform_module(monkeypatch)

    with pytest.raises(module.RobotPlatformError, match="config"):
        module.RobotPlatform(
            robot_id="R1",
            config=object(),
            sdk_adapter=object(),
            heartbeat=object(),
            state_monitor=object(),
            navigator=object(),
        )
