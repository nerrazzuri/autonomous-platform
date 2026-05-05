from __future__ import annotations

from dataclasses import asdict

import pytest

from shared.provisioning.provision_models import (
    ProvisionRequest,
    ProvisionResult,
    RobotConnectionConfig,
    RobotStatus,
    WifiNetwork,
)
from shared.provisioning.roles import get_registered_roles, register_role, unregister_role


def test_wifi_network_construction_and_serialization() -> None:
    network = WifiNetwork(
        ssid="Robot-AP-01",
        signal=-48,
        security="WPA2",
        is_robot_ap=True,
    )

    assert asdict(network) == {
        "ssid": "Robot-AP-01",
        "signal": -48,
        "security": "WPA2",
        "is_robot_ap": True,
    }


def test_provision_request_defaults() -> None:
    request = ProvisionRequest(
        dog_ap_ssid="Robot-AP-01",
        target_wifi_ssid="WarehouseWiFi",
        target_wifi_password="secret-password",
    )

    assert request.role == "logistics"
    assert request.ssh_user == "firefly"
    assert request.pc_wifi_iface is None
    assert request.robot_id is None


def test_provision_result_success_and_failure_construction() -> None:
    success = ProvisionResult(
        success=True,
        robot_id="logistics_01",
        dog_mac="AA:BB:CC:DD:EE:FF",
        dog_ip="192.168.1.101",
        pc_ip="192.168.1.10",
        role="logistics",
        message="Provisioning complete",
    )
    failure = ProvisionResult(
        success=False,
        message="Timed out waiting for robot AP",
    )

    assert asdict(success)["success"] is True
    assert success.robot_id == "logistics_01"
    assert failure.success is False
    assert failure.robot_id is None


def test_robot_connection_config_construction() -> None:
    connection = RobotConnectionConfig(
        robot_id="patrol_01",
        dog_ip="192.168.1.150",
        pc_ip="192.168.1.10",
        sdk_port=43988,
    )

    assert asdict(connection) == {
        "robot_id": "patrol_01",
        "dog_ip": "192.168.1.150",
        "pc_ip": "192.168.1.10",
        "sdk_port": 43988,
        "ssh_user": "firefly",
    }


def test_robot_status_construction() -> None:
    status = RobotStatus(
        robot_id="logistics_01",
        dog_ip="192.168.1.101",
        connected=True,
        provisioned=True,
        message="Robot reachable",
    )

    assert asdict(status) == {
        "robot_id": "logistics_01",
        "dog_ip": "192.168.1.101",
        "connected": True,
        "provisioned": True,
        "message": "Robot reachable",
    }


def test_invalid_role_raises() -> None:
    with pytest.raises(ValueError, match="role"):
        ProvisionRequest(
            dog_ap_ssid="Robot-AP-01",
            target_wifi_ssid="WarehouseWiFi",
            target_wifi_password="secret-password",
            role="security",
        )


def test_registered_custom_role_is_valid() -> None:
    register_role("inspection")
    try:
        request = ProvisionRequest(
            dog_ap_ssid="Robot-AP-01",
            target_wifi_ssid="WarehouseWiFi",
            target_wifi_password="secret-password",
            role="inspection",
        )

        assert request.role == "inspection"
        assert "inspection" in get_registered_roles()
    finally:
        unregister_role("inspection")
