from __future__ import annotations

import importlib
import sys
from textwrap import dedent
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_config_module(monkeypatch: pytest.MonkeyPatch, *, preserve_env: bool = False):
    if not preserve_env:
        monkeypatch.delenv("QUADRUPED_CONFIG_PATH", raising=False)
    monkeypatch.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    sys.modules.pop("shared.core.config", None)
    sys.modules.pop("shared.core", None)
    sys.modules.pop("core.config", None)
    sys.modules.pop("core", None)
    return importlib.import_module("core.config")


def write_yaml(path: Path, content: str) -> None:
    path.write_text(dedent(content).strip() + "\n", encoding="utf-8")


def test_load_defaults_when_file_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_config_module(monkeypatch, preserve_env=True)

    config = module.load_config(tmp_path / "missing.yaml")

    assert config.quadruped.quadruped_ip == "192.168.234.1"
    assert config.api.port == 8080
    assert config.database.sqlite_path == "data/quadruped.db"


def test_load_partial_yaml_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_config_module(monkeypatch, preserve_env=True)
    config_path = tmp_path / "config.yaml"
    write_yaml(
        config_path,
        """
        quadruped:
          quadruped_ip: "192.168.1.88"
        logging:
          level: "debug"
        """,
    )

    config = module.load_config(config_path)

    assert config.quadruped.quadruped_ip == "192.168.1.88"
    assert config.quadruped.sdk_port == 43988
    assert config.logging.level == "DEBUG"


def test_load_quadruped_sdk_lib_path_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_config_module(monkeypatch, preserve_env=True)
    config_path = tmp_path / "config.yaml"
    write_yaml(
        config_path,
        """
        quadruped:
          sdk_lib_path: "/opt/agibot/sdk"
        """,
    )

    config = module.load_config(config_path)

    assert config.quadruped.sdk_lib_path == "/opt/agibot/sdk"


def test_default_config_includes_patrol_and_vision_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_config_module(monkeypatch)

    config = module.AppConfig()

    assert config.patrol.schedule_enabled is True
    assert config.patrol.patrol_interval_seconds == 1800
    assert config.patrol.observation_dwell_seconds == 3.0
    assert config.patrol.anomaly_cooldown_seconds == 300.0
    assert config.patrol.max_consecutive_failures == 3
    assert config.patrol.alert_on_anomaly is True
    assert config.vision.enabled is False
    assert config.vision.provider == "claude"
    assert config.vision.claude_model == "claude-sonnet-4-20250514"
    assert config.vision.claude_max_tokens == 500
    assert config.vision.frame_width == 640
    assert config.vision.frame_height == 480
    assert config.vision.sharpness_threshold == 50.0
    assert config.vision.offline_fallback_mode == "conservative"
    assert config.vision.zones_file == "data/zones.yaml"
    assert config.vision.api_timeout_seconds == 10.0


def test_app_owned_logistics_config_helper_reads_compat_section(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_config_module(monkeypatch)
    from apps.logistics.config import LogisticsSection, get_logistics_config

    config = module.AppConfig()

    logistics = get_logistics_config(config)

    assert isinstance(logistics, LogisticsSection)
    assert logistics.routes_file == config.logistics.routes_file
    assert logistics.allow_placeholder_routes is config.logistics.allow_placeholder_routes


def test_app_owned_patrol_config_helper_reads_compat_section(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_config_module(monkeypatch)
    from apps.patrol.config import PatrolSection, get_patrol_config

    config = module.AppConfig()

    patrol = get_patrol_config(config)

    assert isinstance(patrol, PatrolSection)
    assert patrol.schedule_enabled is config.patrol.schedule_enabled
    assert patrol.patrol_interval_seconds == config.patrol.patrol_interval_seconds
    assert patrol.observation_dwell_seconds == config.patrol.observation_dwell_seconds
    assert patrol.anomaly_cooldown_seconds == config.patrol.anomaly_cooldown_seconds
    assert patrol.max_consecutive_failures == config.patrol.max_consecutive_failures
    assert patrol.alert_on_anomaly is config.patrol.alert_on_anomaly


def test_shared_config_marks_app_sections_compatibility_only() -> None:
    source = (ROOT / "shared" / "core" / "config.py").read_text(encoding="utf-8")

    assert "Deprecated app compatibility section" in source
    assert "Do not add new app-specific config here" in source
    assert "future app config registry" in source


def test_shared_config_does_not_import_app_packages() -> None:
    source = (ROOT / "shared" / "core" / "config.py").read_text(encoding="utf-8")

    assert "from apps" not in source
    assert "import apps" not in source


def test_config_example_includes_patrol_and_vision_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_config_module(monkeypatch, preserve_env=True)

    config = module.load_config(ROOT / "config.yaml.example")

    assert config.patrol.schedule_enabled is True
    assert config.patrol.patrol_interval_seconds == 1800
    assert config.patrol.observation_dwell_seconds == 3.0
    assert config.patrol.anomaly_cooldown_seconds == 300.0
    assert config.patrol.max_consecutive_failures == 3
    assert config.patrol.alert_on_anomaly is True
    assert config.vision.enabled is False
    assert config.vision.provider == "claude"
    assert config.vision.claude_model == "claude-sonnet-4-20250514"
    assert config.vision.claude_max_tokens == 500
    assert config.vision.frame_width == 640
    assert config.vision.frame_height == 480
    assert config.vision.sharpness_threshold == 50.0
    assert config.vision.offline_fallback_mode == "conservative"
    assert config.vision.zones_file == "data/zones.yaml"
    assert config.vision.api_timeout_seconds == 10.0


def test_invalid_port_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_config_module(monkeypatch, preserve_env=True)
    config_path = tmp_path / "config.yaml"
    write_yaml(
        config_path,
        """
        api:
          port: 70000
        """,
    )

    with pytest.raises(module.ConfigError, match="config.yaml"):
        module.load_config(config_path)


def test_invalid_battery_thresholds_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_config_module(monkeypatch)
    config_path = tmp_path / "config.yaml"
    write_yaml(
        config_path,
        """
        battery:
          warn_pct: 20
          critical_pct: 25
        """,
    )

    with pytest.raises(module.ConfigError, match="battery"):
        module.load_config(config_path)


def test_invalid_log_level_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_config_module(monkeypatch)
    config_path = tmp_path / "config.yaml"
    write_yaml(
        config_path,
        """
        logging:
          level: "VERBOSE"
        """,
    )

    with pytest.raises(module.ConfigError, match="logging"):
        module.load_config(config_path)


def test_email_enabled_requires_smtp_host_and_supervisor_email(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_config_module(monkeypatch)
    config_path = tmp_path / "config.yaml"
    write_yaml(
        config_path,
        """
        alerts:
          email_enabled: true
        """,
    )

    with pytest.raises(module.ConfigError, match="supervisor_email"):
        module.load_config(config_path)


def test_get_token_for_role(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_config_module(monkeypatch)

    auth = module.AuthSection(
        operator_token="operator-secret",
        qa_token="qa-secret",
        supervisor_token="supervisor-secret",
    )

    assert auth.get_token_for_role("operator") == "operator-secret"
    assert auth.get_token_for_role("qa") == "qa-secret"
    assert auth.get_token_for_role("supervisor") == "supervisor-secret"
    with pytest.raises(ValueError, match="Unsupported role"):
        auth.get_token_for_role("guest")


def test_path_helpers_return_path_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_config_module(monkeypatch)

    config = module.AppConfig()

    assert config.database_path() == Path("data/quadruped.db")
    assert config.routes_path() == Path("data/routes.json")
    assert config.stations_path() == Path("data/stations.json")
    assert config.log_path() == Path("logs")

    assert isinstance(config.database_path(), Path)
    assert isinstance(config.routes_path(), Path)
    assert isinstance(config.stations_path(), Path)
    assert isinstance(config.log_path(), Path)


def test_default_route_store_files_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_config_module(monkeypatch)

    config = module.AppConfig()

    assert config.routes_path().exists()
    assert config.stations_path().exists()
    assert config.routes_path() != config.logistics_routes_path()


def test_reload_config_replaces_cached_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.yaml"
    write_yaml(
        config_path,
        """
        quadruped:
          quadruped_ip: "192.168.10.10"
        """,
    )
    monkeypatch.setenv("QUADRUPED_CONFIG_PATH", str(config_path))

    module = load_config_module(monkeypatch, preserve_env=True)
    first = module.get_config()
    assert first.quadruped.quadruped_ip == "192.168.10.10"

    write_yaml(
        config_path,
        """
        quadruped:
          quadruped_ip: "192.168.10.11"
        """,
    )

    reloaded = module.reload_config()

    assert reloaded.quadruped.quadruped_ip == "192.168.10.11"
    assert module.get_config() is reloaded
    assert module.CONFIG is reloaded
    assert first is not reloaded
