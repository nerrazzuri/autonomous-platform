from __future__ import annotations

import importlib
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from shared.provisioning.provision_backend import write_robot_entry
from shared.provisioning.provision_models import ProvisionResult


ROOT = Path(__file__).resolve().parents[1]


def load_robot_config_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    module = importlib.import_module("shared.core.robot_config")
    module._ROBOT_CONFIG_CACHE = None
    return module


@pytest.fixture(autouse=True)
def reset_robot_config_cache() -> None:
    module = importlib.import_module("shared.core.robot_config")
    module._ROBOT_CONFIG_CACHE = None
    yield
    module._ROBOT_CONFIG_CACHE = None


def write_yaml(path: Path, content: str) -> None:
    path.write_text(dedent(content).strip() + "\n", encoding="utf-8")


def test_load_valid_config(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_config_module(monkeypatch)

    configs = module.RobotConfigLoader(ROOT / "data" / "robots.yaml").load()

    assert len(configs) == 2
    assert [config.connection.robot_id for config in configs] == ["R1", "R2"]


def test_duplicate_robot_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_config_module(monkeypatch)
    config_path = tmp_path / "robots.yaml"
    write_yaml(
        config_path,
        """
        - robot_id: R1
          connection:
            robot_ip: 192.168.1.101
            sdk_port: 43988
            local_ip: 192.168.1.10
            local_port: 50051
        - robot_id: R1
          connection:
            robot_ip: 192.168.1.102
            sdk_port: 43988
            local_ip: 192.168.1.10
            local_port: 50052
        """,
    )

    with pytest.raises(module.RobotConfigError, match="duplicate.*R1"):
        module.RobotConfigLoader(config_path).load()


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("robot_ip", "999.1.1.1"),
        ("local_ip", "0.0.0.0"),
    ],
)
def test_invalid_ip_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field_name: str, field_value: str
) -> None:
    module = load_robot_config_module(monkeypatch)
    config_path = tmp_path / "robots.yaml"
    write_yaml(
        config_path,
        f"""
        - robot_id: R1
          connection:
            robot_ip: 192.168.1.101
            sdk_port: 43988
            local_ip: 192.168.1.10
            local_port: 50051
        """,
    )

    content = config_path.read_text(encoding="utf-8").replace(f"{field_name}: 192.168.1.10", f"{field_name}: {field_value}")
    content = content.replace(f"{field_name}: 192.168.1.101", f"{field_name}: {field_value}")
    config_path.write_text(content, encoding="utf-8")

    with pytest.raises(module.RobotConfigError, match=field_name):
        module.RobotConfigLoader(config_path).load()


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("sdk_port", 0),
        ("local_port", 70000),
    ],
)
def test_invalid_port_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field_name: str, field_value: int
) -> None:
    module = load_robot_config_module(monkeypatch)
    config_path = tmp_path / "robots.yaml"
    write_yaml(
        config_path,
        f"""
        - robot_id: R1
          connection:
            robot_ip: 192.168.1.101
            sdk_port: 43988
            local_ip: 192.168.1.10
            local_port: 50051
        """,
    )

    content = config_path.read_text(encoding="utf-8").replace(f"{field_name}: 43988", f"{field_name}: {field_value}")
    content = content.replace(f"{field_name}: 50051", f"{field_name}: {field_value}")
    config_path.write_text(content, encoding="utf-8")

    with pytest.raises(module.RobotConfigError, match=field_name):
        module.RobotConfigLoader(config_path).load()


def test_missing_connection_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_config_module(monkeypatch)
    config_path = tmp_path / "robots.yaml"
    write_yaml(
        config_path,
        """
        - robot_id: R1
          capabilities:
            camera: true
        """,
    )

    with pytest.raises(module.RobotConfigError, match="connection"):
        module.RobotConfigLoader(config_path).load()


def test_missing_robot_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_config_module(monkeypatch)
    config_path = tmp_path / "robots.yaml"
    write_yaml(
        config_path,
        """
        - connection:
            robot_ip: 192.168.1.101
            sdk_port: 43988
            local_ip: 192.168.1.10
            local_port: 50051
        """,
    )

    with pytest.raises(module.RobotConfigError, match="robot_id"):
        module.RobotConfigLoader(config_path).load()


def test_default_capabilities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_config_module(monkeypatch)
    config_path = tmp_path / "robots.yaml"
    write_yaml(
        config_path,
        """
        - robot_id: R1
          connection:
            robot_ip: 192.168.1.101
            sdk_port: 43988
            local_ip: 192.168.1.10
            local_port: 50051
        """,
    )

    configs = module.RobotConfigLoader(config_path).load()

    assert len(configs) == 1
    assert configs[0].capabilities == module.RobotCapabilityConfig()


def test_get_robot_configs_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_config_module(monkeypatch)

    first = module.get_robot_configs()
    second = module.get_robot_configs()

    assert first is second


def test_empty_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_config_module(monkeypatch)
    config_path = tmp_path / "robots.yaml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(module.RobotConfigError, match="empty"):
        module.RobotConfigLoader(config_path).load()


def test_non_list_root_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_config_module(monkeypatch)
    config_path = tmp_path / "robots.yaml"
    write_yaml(
        config_path,
        """
        robots:
          robot_id: R1
        """,
    )

    with pytest.raises(module.RobotConfigError, match="list"):
        module.RobotConfigLoader(config_path).load()


def test_loads_provisioning_written_robots_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_robot_config_module(monkeypatch)
    config_path = tmp_path / "robots.yaml"

    write_robot_entry(
        ProvisionResult(
            success=True,
            robot_id="logistics_01",
            dog_mac="aa:bb:cc:dd:ee:01",
            dog_ip="192.168.1.50",
        ),
        "logistics",
        config_path,
        display_name="Logistics Robot 1",
    )

    configs = module.RobotConfigLoader(config_path).load()

    assert len(configs) == 1
    assert configs[0].connection.robot_id == "logistics_01"
    assert configs[0].mac == "aa:bb:cc:dd:ee:01"
    assert configs[0].quadruped_ip == "192.168.1.50"
    assert configs[0].role == "logistics"
    assert configs[0].enabled is True
