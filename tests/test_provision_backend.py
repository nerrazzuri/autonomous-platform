from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import shared.provisioning.provision_backend as provision_backend
from shared.provisioning.provision_backend import ProvisioningError, write_robot_entry
from shared.provisioning.provision_models import ProvisionRequest, ProvisionResult
from shared.provisioning.roles import register_role, unregister_role


def load_yaml(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_missing_robots_yaml_creates_file(tmp_path: Path) -> None:
    robots_yaml_path = tmp_path / "robots.yaml"

    entry = write_robot_entry(
        ProvisionResult(
            success=True,
            quadruped_mac="aa:bb:cc:dd:ee:01",
            quadruped_ip="192.168.1.50",
        ),
        "logistics",
        robots_yaml_path,
        display_name="Logistics Robot 1",
    )

    data = load_yaml(robots_yaml_path)

    assert robots_yaml_path.exists()
    assert entry["robot_id"] == "logistics_01"
    assert data == {
        "robots": [
            {
                "robot_id": "logistics_01",
                "display_name": "Logistics Robot 1",
                "mac": "aa:bb:cc:dd:ee:01",
                "quadruped_ip": "192.168.1.50",
                "role": "logistics",
                "sdk_lib_path": "sdk/zsl-1",
                "enabled": True,
            }
        ]
    }


def test_append_new_robot_preserves_existing_entries(tmp_path: Path) -> None:
    robots_yaml_path = tmp_path / "robots.yaml"
    robots_yaml_path.write_text(
        yaml.safe_dump(
            {
                "robots": [
                    {
                        "robot_id": "logistics_01",
                        "display_name": "Logistics Robot 1",
                        "mac": "aa:bb:cc:dd:ee:01",
                        "quadruped_ip": "192.168.1.50",
                        "role": "logistics",
                        "sdk_lib_path": "sdk/zsl-1",
                        "enabled": True,
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    write_robot_entry(
        ProvisionResult(
            success=True,
            quadruped_mac="aa:bb:cc:dd:ee:02",
            quadruped_ip="192.168.1.51",
        ),
        "logistics",
        robots_yaml_path,
        display_name="Logistics Robot 2",
    )

    data = load_yaml(robots_yaml_path)

    assert data["robots"] == [
        {
            "robot_id": "logistics_01",
            "display_name": "Logistics Robot 1",
            "mac": "aa:bb:cc:dd:ee:01",
            "quadruped_ip": "192.168.1.50",
            "role": "logistics",
            "sdk_lib_path": "sdk/zsl-1",
            "enabled": True,
        },
        {
            "robot_id": "logistics_02",
            "display_name": "Logistics Robot 2",
            "mac": "aa:bb:cc:dd:ee:02",
            "quadruped_ip": "192.168.1.51",
            "role": "logistics",
            "sdk_lib_path": "sdk/zsl-1",
            "enabled": True,
        },
    ]


def test_same_mac_updates_existing_entry_without_duplicate(tmp_path: Path) -> None:
    robots_yaml_path = tmp_path / "robots.yaml"
    robots_yaml_path.write_text(
        yaml.safe_dump(
            {
                "robots": [
                    {
                        "robot_id": "logistics_01",
                        "display_name": "Original Name",
                        "mac": "aa:bb:cc:dd:ee:01",
                        "quadruped_ip": "192.168.1.50",
                        "role": "logistics",
                        "sdk_lib_path": "sdk/zsl-1",
                        "enabled": True,
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    entry = write_robot_entry(
        ProvisionResult(
            success=True,
            quadruped_mac="aa:bb:cc:dd:ee:01",
            quadruped_ip="192.168.1.99",
        ),
        "patrol",
        robots_yaml_path,
    )

    data = load_yaml(robots_yaml_path)

    assert entry["robot_id"] == "logistics_01"
    assert data["robots"] == [
        {
            "robot_id": "logistics_01",
            "display_name": "Original Name",
            "mac": "aa:bb:cc:dd:ee:01",
            "quadruped_ip": "192.168.1.99",
            "role": "patrol",
            "sdk_lib_path": "sdk/zsl-1",
            "enabled": True,
        }
    ]


def test_explicit_robot_id_is_used(tmp_path: Path) -> None:
    robots_yaml_path = tmp_path / "robots.yaml"

    entry = write_robot_entry(
        ProvisionResult(
            success=True,
            robot_id="custom_01",
            quadruped_mac="aa:bb:cc:dd:ee:03",
            quadruped_ip="192.168.1.52",
        ),
        "patrol",
        robots_yaml_path,
    )

    assert entry["robot_id"] == "custom_01"
    assert load_yaml(robots_yaml_path)["robots"][0]["robot_id"] == "custom_01"


def test_generated_robot_id_avoids_collisions(tmp_path: Path) -> None:
    robots_yaml_path = tmp_path / "robots.yaml"
    robots_yaml_path.write_text(
        yaml.safe_dump(
            {
                "robots": [
                    {
                        "robot_id": "logistics_01",
                        "mac": "aa:bb:cc:dd:ee:01",
                        "quadruped_ip": "192.168.1.50",
                        "role": "logistics",
                        "sdk_lib_path": "sdk/zsl-1",
                        "enabled": True,
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    entry = write_robot_entry(
        ProvisionResult(
            success=True,
            quadruped_mac="aa:bb:cc:dd:ee:02",
            quadruped_ip="192.168.1.51",
        ),
        "logistics",
        robots_yaml_path,
    )

    assert entry["robot_id"] == "logistics_02"


def test_invalid_role_raises(tmp_path: Path) -> None:
    with pytest.raises(ProvisioningError, match="role"):
        write_robot_entry(
            ProvisionResult(
                success=True,
                quadruped_mac="aa:bb:cc:dd:ee:01",
                quadruped_ip="192.168.1.50",
            ),
            "security",
            tmp_path / "robots.yaml",
        )


def test_registered_custom_role_writes_robot_entry(tmp_path: Path) -> None:
    register_role("inspection")
    try:
        entry = write_robot_entry(
            ProvisionResult(
                success=True,
                quadruped_mac="aa:bb:cc:dd:ee:01",
                quadruped_ip="192.168.1.50",
            ),
            "inspection",
            tmp_path / "robots.yaml",
        )

        assert entry["role"] == "inspection"
        assert entry["robot_id"] == "inspection_01"
    finally:
        unregister_role("inspection")


def test_unsuccessful_provision_result_raises(tmp_path: Path) -> None:
    with pytest.raises(ProvisioningError, match="success"):
        write_robot_entry(
            ProvisionResult(
                success=False,
                quadruped_mac="aa:bb:cc:dd:ee:01",
                quadruped_ip="192.168.1.50",
            ),
            "logistics",
            tmp_path / "robots.yaml",
        )


@pytest.mark.parametrize(
    ("quadruped_mac", "quadruped_ip", "expected_match"),
    [
        (None, "192.168.1.50", "quadruped_mac"),
        ("aa:bb:cc:dd:ee:01", None, "quadruped_ip"),
    ],
)
def test_missing_mac_or_ip_raises(
    tmp_path: Path,
    quadruped_mac: str | None,
    quadruped_ip: str | None,
    expected_match: str,
) -> None:
    with pytest.raises(ProvisioningError, match=expected_match):
        write_robot_entry(
            ProvisionResult(
                success=True,
                quadruped_mac=quadruped_mac,
                quadruped_ip=quadruped_ip,
            ),
            "logistics",
            tmp_path / "robots.yaml",
        )


def test_existing_disabled_robot_stays_disabled_when_updated(tmp_path: Path) -> None:
    robots_yaml_path = tmp_path / "robots.yaml"
    robots_yaml_path.write_text(
        yaml.safe_dump(
            {
                "robots": [
                    {
                        "robot_id": "patrol_01",
                        "display_name": "Patrol Robot 1",
                        "mac": "aa:bb:cc:dd:ee:04",
                        "quadruped_ip": "192.168.1.60",
                        "role": "patrol",
                        "sdk_lib_path": "sdk/zsl-1",
                        "enabled": False,
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    write_robot_entry(
        ProvisionResult(
            success=True,
            quadruped_mac="aa:bb:cc:dd:ee:04",
            quadruped_ip="192.168.1.61",
        ),
        "patrol",
        robots_yaml_path,
        display_name="Updated Patrol Robot 1",
    )

    assert load_yaml(robots_yaml_path)["robots"] == [
        {
            "robot_id": "patrol_01",
            "display_name": "Updated Patrol Robot 1",
            "mac": "aa:bb:cc:dd:ee:04",
            "quadruped_ip": "192.168.1.61",
            "role": "patrol",
            "sdk_lib_path": "sdk/zsl-1",
            "enabled": False,
        }
    ]


def test_scan_wifi_networks_parses_nmcli_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="OfficeWiFi:78:WPA2\nGuestWiFi:40:\n",
            stderr="",
        )

    monkeypatch.setattr(provision_backend.subprocess, "run", fake_run)

    networks = provision_backend.scan_wifi_networks()

    assert [network.ssid for network in networks] == ["OfficeWiFi", "GuestWiFi"]
    assert networks[0].signal == 78
    assert networks[0].security == "WPA2"
    assert networks[1].signal == 40
    assert networks[1].security is None


def test_scan_wifi_networks_marks_robot_ap(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="D1-Ultra\\:aa\\:bb\\:cc\\:dd\\:ee:91:WPA2\nAgibot-Test:65:WPA2\n",
            stderr="",
        )

    monkeypatch.setattr(provision_backend.subprocess, "run", fake_run)

    networks = provision_backend.scan_wifi_networks()

    assert networks[0].ssid == "D1-Ultra:aa:bb:cc:dd:ee"
    assert networks[0].is_robot_ap is True
    assert networks[1].is_robot_ap is True


def test_find_ip_by_mac_finds_ip_from_ip_neigh(monkeypatch: pytest.MonkeyPatch) -> None:
    monotonic_values = iter([0.0, 0.2])

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "192.168.1.77 dev wlan0 lladdr aa:bb:cc:dd:ee:77 REACHABLE\n"
                "192.168.1.88 dev wlan0 lladdr aa:bb:cc:dd:ee:88 STALE\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(provision_backend.subprocess, "run", fake_run)
    monkeypatch.setattr(provision_backend.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(provision_backend.time, "sleep", lambda _seconds: None)

    found_ip = provision_backend.find_ip_by_mac("AA:BB:CC:DD:EE:88", timeout=1.0, poll_interval=0.01)

    assert found_ip == "192.168.1.88"


def test_find_ip_by_mac_raises_when_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monotonic_values = iter([0.0, 0.2, 0.4, 0.6])

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="192.168.1.77 dev wlan0 lladdr aa:bb:cc:dd:ee:77 REACHABLE\n",
            stderr="",
        )

    monkeypatch.setattr(provision_backend.subprocess, "run", fake_run)
    monkeypatch.setattr(provision_backend.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(provision_backend.time, "sleep", lambda _seconds: None)

    with pytest.raises(ProvisioningError, match="aa:bb:cc:dd:ee:99"):
        provision_backend.find_ip_by_mac("aa:bb:cc:dd:ee:99", timeout=0.5, poll_interval=0.01)


def test_ensure_remote_quadruped_script_uploads_and_chmods(monkeypatch: pytest.MonkeyPatch) -> None:
    uploads: list[tuple[str, str]] = []
    commands: list[str] = []

    class FakeSFTP:
        def put(self, local_path: str, remote_path: str) -> None:
            uploads.append((local_path, remote_path))

        def close(self) -> None:
            return None

    class FakeClient:
        def open_sftp(self) -> FakeSFTP:
            return FakeSFTP()

    def fake_run_remote_command(_client, command: str, **_kwargs):
        commands.append(command)
        return ""

    monkeypatch.setattr(provision_backend, "run_remote_command", fake_run_remote_command)

    provision_backend.ensure_remote_quadruped_script(FakeClient())

    assert uploads
    assert uploads[0][0].endswith("scripts/quadruped_wifi_provision.sh")
    assert uploads[0][1] == "/usr/local/bin/quadruped_wifi_provision.sh"
    assert commands == ["sudo chmod +x /usr/local/bin/quadruped_wifi_provision.sh"]


def test_patch_sdk_config_issues_expected_remote_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[str] = []
    closed = {"value": False}

    class FakeClient:
        def close(self) -> None:
            closed["value"] = True

    def fake_ssh_connect(host: str, username: str, password: str | None, timeout: float = 10.0):
        assert host == "192.168.1.50"
        assert username == "firefly"
        assert password == "pw"
        assert timeout == 10.0
        return FakeClient()

    def fake_run_remote_command(_client, command: str, **_kwargs):
        commands.append(command)
        return ""

    monkeypatch.setattr(provision_backend, "ssh_connect", fake_ssh_connect)
    monkeypatch.setattr(provision_backend, "run_remote_command", fake_run_remote_command)

    provision_backend.patch_sdk_config("192.168.1.50", "192.168.1.10", username="firefly", password="pw")

    assert closed["value"] is True
    assert len(commands) == 2
    assert "sdk_config.yaml.bak." in commands[0]
    assert "192.168.1.10" in commands[1]
    assert "target_ip" in commands[1]


def test_get_pc_ip_for_target_returns_mocked_local_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSocket:
        def connect(self, _address: tuple[str, int]) -> None:
            return None

        def getsockname(self) -> tuple[str, int]:
            return ("192.168.1.10", 45555)

        def close(self) -> None:
            return None

    monkeypatch.setattr(provision_backend.socket, "socket", lambda *_args, **_kwargs: FakeSocket())

    assert provision_backend.get_pc_ip_for_target("192.168.1.50") == "192.168.1.10"


def test_provision_quadruped_happy_path_with_mocked_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeClient:
        def close(self) -> None:
            calls.append(("close", None))

    client = FakeClient()

    def fake_ssh_connect(host: str, username: str, password: str | None, timeout: float = 10.0):
        calls.append(("ssh_connect", (host, username, password, timeout)))
        return client

    def fake_ensure_remote_quadruped_script(ssh_client) -> None:
        calls.append(("ensure_remote_quadruped_script", ssh_client))

    def fake_run_remote_command(_client, command: str, **_kwargs):
        calls.append(("run_remote_command", command))
        if command.startswith("sudo /usr/local/bin/quadruped_wifi_provision.sh"):
            assert "'FACTORY_WIFI'" in command
            assert "'secret'" in command
            return ""
        if command == "cat /tmp/quadruped_mac":
            return "aa:bb:cc:dd:ee:01\n"
        if command == "cat /tmp/quadruped_ip":
            return "192.168.1.50\n"
        raise AssertionError(f"Unexpected command: {command}")

    def fake_find_ip_by_mac(mac_address: str, **_kwargs):
        calls.append(("find_ip_by_mac", mac_address))
        return "192.168.1.50"

    def fake_get_pc_ip_for_target(target_ip: str) -> str:
        calls.append(("get_pc_ip_for_target", target_ip))
        return "192.168.1.10"

    def fake_patch_sdk_config(robot_ip: str, pc_ip: str, *, username: str, password: str | None) -> None:
        calls.append(("patch_sdk_config", (robot_ip, pc_ip, username, password)))

    monkeypatch.setattr(provision_backend, "ssh_connect", fake_ssh_connect)
    monkeypatch.setattr(provision_backend, "ensure_remote_quadruped_script", fake_ensure_remote_quadruped_script)
    monkeypatch.setattr(provision_backend, "run_remote_command", fake_run_remote_command)
    monkeypatch.setattr(provision_backend, "find_ip_by_mac", fake_find_ip_by_mac)
    monkeypatch.setattr(provision_backend, "get_pc_ip_for_target", fake_get_pc_ip_for_target)
    monkeypatch.setattr(provision_backend, "patch_sdk_config", fake_patch_sdk_config)

    result = provision_backend.provision_quadruped(
        ProvisionRequest(
            quadruped_ap_ssid="D1-Ultra:aa:bb:cc:dd:ee:01",
            target_wifi_ssid="FACTORY_WIFI",
            target_wifi_password="secret",
            role="logistics",
            pc_wifi_iface="wlan0",
            robot_id="logistics_01",
            ssh_password="pw",
        )
    )

    assert result.success is True
    assert result.robot_id == "logistics_01"
    assert result.quadruped_mac == "aa:bb:cc:dd:ee:01"
    assert result.quadruped_ip == "192.168.1.50"
    assert result.pc_ip == "192.168.1.10"
    assert result.role == "logistics"
    assert ("patch_sdk_config", ("192.168.1.50", "192.168.1.10", "firefly", "pw")) in calls


def test_provision_quadruped_failure_path_returns_unsuccessful_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ssh_connect(*_args, **_kwargs):
        raise ProvisioningError("SSH connect failed")

    monkeypatch.setattr(provision_backend, "ssh_connect", fake_ssh_connect)

    result = provision_backend.provision_quadruped(
        ProvisionRequest(
            quadruped_ap_ssid="D1-Ultra:aa:bb:cc:dd:ee:01",
            target_wifi_ssid="FACTORY_WIFI",
            target_wifi_password="secret",
            role="logistics",
            pc_wifi_iface="wlan0",
        )
    )

    assert result.success is False
    assert "SSH connect failed" in (result.message or "")


def test_backend_does_not_log_wifi_password_on_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_ssh_connect(*_args, **_kwargs):
        raise ProvisioningError("SSH connect failed")

    monkeypatch.setattr(provision_backend, "ssh_connect", fake_ssh_connect)

    provision_backend.provision_quadruped(
        ProvisionRequest(
            quadruped_ap_ssid="D1-Ultra:aa:bb:cc:dd:ee:01",
            target_wifi_ssid="FACTORY_WIFI",
            target_wifi_password="secret",
            role="logistics",
            pc_wifi_iface="wlan0",
        )
    )

    captured = capsys.readouterr()
    assert "secret" not in captured.out
    assert "secret" not in captured.err
