from __future__ import annotations

import importlib.util
import stat
from pathlib import Path

import pytest
import yaml


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts/setup/create_poc_local_config.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("create_poc_local_config", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generates_config_and_replaces_placeholder_tokens(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = load_script_module()
    template = _write_template(tmp_path)
    output = tmp_path / "config.local.yaml"

    result = module.main(["--template", str(template), "--output", str(output)])

    assert result == 0
    config = _read_yaml(output)
    assert config["auth"]["operator_token"] != "__OPERATOR_TOKEN__"
    assert config["auth"]["qa_token"] != "__QA_TOKEN__"
    assert config["auth"]["supervisor_token"] != "__SUPERVISOR_TOKEN__"
    assert len(config["auth"]["operator_token"]) > 20
    assert "OPERATOR_TOKEN=" not in capsys.readouterr().out


def test_refuses_overwrite_without_force(tmp_path: Path) -> None:
    module = load_script_module()
    template = _write_template(tmp_path)
    output = tmp_path / "config.local.yaml"
    output.write_text("existing: true\n", encoding="utf-8")

    result = module.main(["--template", str(template), "--output", str(output)])

    assert result == 1
    assert output.read_text(encoding="utf-8") == "existing: true\n"


def test_allows_overwrite_with_force(tmp_path: Path) -> None:
    module = load_script_module()
    template = _write_template(tmp_path)
    output = tmp_path / "config.local.yaml"
    output.write_text("existing: true\n", encoding="utf-8")

    result = module.main(["--template", str(template), "--output", str(output), "--force"])

    assert result == 0
    assert _read_yaml(output)["ros2"]["enabled"] is True


def test_prints_tokens_only_when_requested(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = load_script_module()
    template = _write_template(tmp_path)
    output = tmp_path / "config.local.yaml"

    result = module.main(["--template", str(template), "--output", str(output), "--print-tokens"])

    assert result == 0
    stdout = capsys.readouterr().out
    assert "Store these securely" in stdout
    assert "OPERATOR_TOKEN=" in stdout
    assert "QA_TOKEN=" in stdout
    assert "SUPERVISOR_TOKEN=" in stdout


def test_sets_poc_defaults_and_cli_overrides(tmp_path: Path) -> None:
    module = load_script_module()
    template = _write_template(tmp_path)
    output = tmp_path / "config.local.yaml"

    result = module.main(
        [
            "--template",
            str(template),
            "--output",
            str(output),
            "--workstation-ip",
            "192.168.1.10",
            "--quadruped-ip",
            "192.168.1.20",
            "--sdk-lib-path",
            "sdk/zsl-1w",
            "--speaker-enabled",
            "true",
            "--allow-placeholder-routes",
            "false",
            "--position-source",
            "odometry",
        ]
    )

    assert result == 0
    config = _read_yaml(output)
    assert config["workstation"]["local_ip"] == "192.168.1.10"
    assert config["workstation"]["lan_ip"] == "192.168.1.10"
    assert config["quadruped"]["quadruped_ip"] == "192.168.1.20"
    assert config["quadruped"]["sdk_lib_path"] == "sdk/zsl-1w"
    assert config["ros2"]["enabled"] is True
    assert config["ros2"]["scan_topic"] == "/scan"
    assert config["ros2"]["pose_topic"] == "/pose"
    assert config["ros2"]["odom_topic"] == "/odom"
    assert config["ros2"]["odom_frame"] == "odom"
    assert config["ros2"]["base_frame"] == "BASE_LINK"
    assert config["navigation"]["position_source"] == "odometry"
    assert config["logistics"]["allow_placeholder_routes"] is False
    assert config["speaker"]["enabled"] is True


def test_defaults_to_slam_and_placeholder_routes_allowed(tmp_path: Path) -> None:
    module = load_script_module()
    template = _write_template(tmp_path)
    output = tmp_path / "config.local.yaml"

    result = module.main(["--template", str(template), "--output", str(output)])

    assert result == 0
    config = _read_yaml(output)
    assert config["navigation"]["position_source"] == "slam"
    assert config["logistics"]["allow_placeholder_routes"] is True


def test_output_permissions_are_user_only_when_supported(tmp_path: Path) -> None:
    module = load_script_module()
    template = _write_template(tmp_path)
    output = tmp_path / "config.local.yaml"

    result = module.main(["--template", str(template), "--output", str(output)])

    assert result == 0
    mode = stat.S_IMODE(output.stat().st_mode)
    assert mode == 0o600


def _write_template(tmp_path: Path) -> Path:
    template = tmp_path / "template.yaml"
    template.write_text(
        yaml.safe_dump(
            {
                "quadruped": {
                    "quadruped_ip": "__QUADRUPED_IP__",
                    "sdk_port": 43988,
                    "sdk_lib_path": "sdk/zsl-1",
                },
                "workstation": {
                    "local_ip": "__LOCAL_IP__",
                    "lan_ip": "__LOCAL_IP__",
                },
                "logistics": {"allow_placeholder_routes": True},
                "navigation": {"position_source": "odometry"},
                "auth": {
                    "operator_token": "__OPERATOR_TOKEN__",
                    "qa_token": "__QA_TOKEN__",
                    "supervisor_token": "__SUPERVISOR_TOKEN__",
                },
                "ros2": {
                    "enabled": False,
                    "scan_topic": "/old_scan",
                    "pose_topic": "/old_pose",
                    "odom_topic": "/old_odom",
                    "odom_frame": "old_odom",
                    "base_frame": "base_link",
                },
                "speaker": {"enabled": False},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return template


def _read_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)
