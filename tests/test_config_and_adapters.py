import types

from autonomous_logistic.adapters.agibot_d1 import AgibotD1Adapter
from autonomous_logistic.api.app import build_robot_adapter
from autonomous_logistic.config.settings import AppSettings, RobotSettings
from autonomous_logistic.core.errors import RobotAdapterUnavailable
from autonomous_logistic.simulation_or_mock.mock_robot import MockRobotAdapter


def test_capability_environment_override_controls_feature_flags(monkeypatch):
    monkeypatch.setenv("AL_HAS_LIDAR", "true")
    monkeypatch.setenv("AL_HAS_SCREEN", "1")
    monkeypatch.setenv("AL_HAS_REMOTE_DISPATCH", "yes")

    settings = AppSettings.from_sources(config_path=None)

    assert settings.capabilities.has_lidar is True
    assert settings.capabilities.has_screen is True
    assert settings.capabilities.has_remote_dispatch is True
    assert settings.capabilities.has_speaker is False


def test_mock_robot_adapter_reports_deterministic_status():
    robot = MockRobotAdapter()

    robot.move("forward", speed=0.4)
    navigation = robot.navigate_to("STATION_A")
    robot.pause()
    health = robot.get_health_status()
    sensors = robot.get_sensor_status()

    assert navigation.accepted is True
    assert navigation.target == "STATION_A"
    assert health["mode"] == "mock"
    assert health["paused"] is True
    assert sensors["obstacle_detected"] is False


def test_agibot_adapter_seam_fails_clearly_without_sdk():
    adapter = AgibotD1Adapter(sdk_module_name="missing_agibot_sdk_module")

    try:
        adapter.connect()
    except RobotAdapterUnavailable as error:
        assert "missing_agibot_sdk_module" in str(error)
    else:
        raise AssertionError("AgibotD1Adapter.connect() must fail without SDK")


def test_build_robot_adapter_fails_when_configured_agibot_sdk_is_missing():
    settings = AppSettings(
        robot=RobotSettings(adapter="agibot_d1", sdk_module_name="missing_agibot_sdk_module")
    )

    try:
        build_robot_adapter(settings)
    except RobotAdapterUnavailable as error:
        assert "missing_agibot_sdk_module" in str(error)
    else:
        raise AssertionError("build_robot_adapter() must fail without configured Agibot SDK")


def test_agibot_adapter_rejects_non_high_control_level():
    try:
        AgibotD1Adapter(sdk_module_name="agibot_sdk", control_level="low")
    except RobotAdapterUnavailable as error:
        assert "control_level" in str(error)
        assert "high" in str(error)
    else:
        raise AssertionError("AgibotD1Adapter must reject non-high control levels")


def test_build_robot_adapter_connects_agibot_and_reports_configured_control_level(monkeypatch):
    sdk_module_name = "fake_agibot_sdk"
    monkeypatch.setitem(__import__("sys").modules, sdk_module_name, types.ModuleType(sdk_module_name))
    settings = AppSettings(
        robot=RobotSettings(
            adapter="agibot_d1",
            sdk_module_name=sdk_module_name,
            robot_ip="10.0.0.10",
            client_ip="10.0.0.20",
            sdk_port=44000,
            control_level="high",
        )
    )

    adapter = build_robot_adapter(settings)
    health = adapter.get_health_status()

    assert health["connected"] is True
    assert health["robot_ip"] == "10.0.0.10"
    assert health["client_ip"] == "10.0.0.20"
    assert health["sdk_port"] == 44000
    assert health["control_level"] == "high"
