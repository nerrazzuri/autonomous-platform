from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from shared.provisioning.provision_models import ProvisionResult


ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / "scripts" / "provision_cli.py"


def load_cli_module():
    module_name = "tests._provision_cli"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, CLI_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_yaml(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_dry_run_succeeds_without_calling_backend(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_cli_module()
    called = False

    def fake_provision_dog(_request):
        nonlocal called
        called = True
        raise AssertionError("provision_dog should not be called during dry-run")

    monkeypatch.setattr(module.provision_backend, "provision_dog", fake_provision_dog)
    robots_yaml_path = tmp_path / "robots.yaml"

    exit_code = module.main(
        [
            "--dog-ap-ssid",
            "D1-Ultra:aa:bb:cc:dd:ee",
            "--target-wifi-ssid",
            "FACTORY_WIFI",
            "--target-wifi-password",
            "secret",
            "--role",
            "logistics",
            "--pc-wifi-iface",
            "wlan0",
            "--robots-yaml-path",
            str(robots_yaml_path),
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert called is False
    assert not robots_yaml_path.exists()
    assert "dry-run" in captured.out.lower()
    assert "D1-Ultra:aa:bb:cc:dd:ee" in captured.out
    assert "FACTORY_WIFI" in captured.out
    assert "logistics" in captured.out
    assert "secret" not in captured.out
    assert "secret" not in captured.err


def test_successful_mocked_provisioning_writes_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_cli_module()
    robots_yaml_path = tmp_path / "robots.yaml"

    def fake_provision_dog(_request):
        return ProvisionResult(
            success=True,
            robot_id="logistics_01",
            dog_mac="aa:bb:cc:dd:ee:01",
            dog_ip="192.168.1.50",
            pc_ip="192.168.1.10",
            role="logistics",
        )

    monkeypatch.setattr(module.provision_backend, "provision_dog", fake_provision_dog)

    exit_code = module.main(
        [
            "--dog-ap-ssid",
            "D1-Ultra:aa:bb:cc:dd:ee",
            "--target-wifi-ssid",
            "FACTORY_WIFI",
            "--target-wifi-password",
            "secret",
            "--role",
            "logistics",
            "--pc-wifi-iface",
            "wlan0",
            "--display-name",
            "Logistics Robot 1",
            "--robots-yaml-path",
            str(robots_yaml_path),
        ]
    )

    captured = capsys.readouterr()
    data = load_yaml(robots_yaml_path)

    assert exit_code == 0
    assert robots_yaml_path.exists()
    assert data["robots"][0]["robot_id"] == "logistics_01"
    assert data["robots"][0]["display_name"] == "Logistics Robot 1"
    assert "logistics_01" in captured.out
    assert "aa:bb:cc:dd:ee:01" in captured.out
    assert "192.168.1.50" in captured.out
    assert str(robots_yaml_path) in captured.out
    assert "secret" not in captured.out
    assert "secret" not in captured.err


def test_failed_provisioning_result_exits_non_zero_and_does_not_write_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_cli_module()
    robots_yaml_path = tmp_path / "robots.yaml"

    def fake_provision_dog(_request):
        return ProvisionResult(
            success=False,
            message="Robot refused provisioning",
        )

    monkeypatch.setattr(module.provision_backend, "provision_dog", fake_provision_dog)

    exit_code = module.main(
        [
            "--dog-ap-ssid",
            "D1-Ultra:aa:bb:cc:dd:ee",
            "--target-wifi-ssid",
            "FACTORY_WIFI",
            "--target-wifi-password",
            "secret",
            "--role",
            "logistics",
            "--pc-wifi-iface",
            "wlan0",
            "--robots-yaml-path",
            str(robots_yaml_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code != 0
    assert not robots_yaml_path.exists()
    assert "Robot refused provisioning" in captured.err
    assert "secret" not in captured.out
    assert "secret" not in captured.err


def test_not_implemented_backend_exits_non_zero_with_clear_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_cli_module()

    def fake_provision_dog(_request):
        raise NotImplementedError("not yet wired")

    monkeypatch.setattr(module.provision_backend, "provision_dog", fake_provision_dog)

    exit_code = module.main(
        [
            "--dog-ap-ssid",
            "D1-Ultra:aa:bb:cc:dd:ee",
            "--target-wifi-ssid",
            "FACTORY_WIFI",
            "--target-wifi-password",
            "secret",
            "--role",
            "logistics",
            "--pc-wifi-iface",
            "wlan0",
            "--robots-yaml-path",
            str(tmp_path / "robots.yaml"),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code != 0
    assert "Real provisioning backend is not implemented yet." in captured.err
    assert "secret" not in captured.out
    assert "secret" not in captured.err


def test_invalid_role_exits_non_zero(capsys: pytest.CaptureFixture[str]) -> None:
    module = load_cli_module()

    with pytest.raises(SystemExit) as exc_info:
        module.main(
            [
                "--dog-ap-ssid",
                "D1-Ultra:aa:bb:cc:dd:ee",
                "--target-wifi-ssid",
                "FACTORY_WIFI",
                "--target-wifi-password",
                "secret",
                "--role",
                "security",
                "--pc-wifi-iface",
                "wlan0",
            ]
        )

    captured = capsys.readouterr()

    assert exc_info.value.code != 0
    assert "invalid choice" in captured.err
    assert "secret" not in captured.out
    assert "secret" not in captured.err


def test_explicit_robot_id_and_display_name_are_passed_through_to_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_cli_module()
    robots_yaml_path = tmp_path / "robots.yaml"

    def fake_provision_dog(_request):
        return ProvisionResult(
            success=True,
            robot_id="custom_01",
            dog_mac="aa:bb:cc:dd:ee:09",
            dog_ip="192.168.1.90",
            role="patrol",
        )

    monkeypatch.setattr(module.provision_backend, "provision_dog", fake_provision_dog)

    exit_code = module.main(
        [
            "--dog-ap-ssid",
            "D1-Ultra:aa:bb:cc:dd:ee",
            "--target-wifi-ssid",
            "FACTORY_WIFI",
            "--target-wifi-password",
            "secret",
            "--role",
            "patrol",
            "--pc-wifi-iface",
            "wlan0",
            "--robot-id",
            "custom_01",
            "--display-name",
            "Patrol Robot 1",
            "--robots-yaml-path",
            str(robots_yaml_path),
        ]
    )

    captured = capsys.readouterr()
    data = load_yaml(robots_yaml_path)

    assert exit_code == 0
    assert data["robots"][0]["robot_id"] == "custom_01"
    assert data["robots"][0]["display_name"] == "Patrol Robot 1"
    assert "custom_01" in captured.out
    assert "secret" not in captured.out
    assert "secret" not in captured.err
